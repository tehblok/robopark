# Robopark Administration and Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пересобрать вход, регистрацию, состояния доступа и весь административный контур Robopark в канонические адаптивные модули с безопасной работой с секретами, проверяемым аудитом и едиными подтверждениями опасных действий.

**Architecture:** План выполняется после Foundation и использует его `RouteManifest`, `AccessPolicy`, AppShell и design-system primitives; transport остаётся в `apps/web/src/api.ts`, а продуктовая логика размещается в `domains/onboarding` и `domains/administration`. Существующие FastAPI-контракты сохраняются, а backend получает additive revision tokens, атомарный audit для транзакционных административных mutations и проверяемые, датированные статусы интеграций. Старые `/admin*` URL остаются тонкими redirect-adapters до Этапа 5.

**Tech Stack:** Python 3.12.13, FastAPI, SQLAlchemy 2, pytest, Ruff, React 19, TypeScript 6, React Router 7, Vitest, Testing Library, Playwright, `@axe-core/playwright`, CSS custom properties, Vite 8, npm, uv.

**Spec:** [`docs/superpowers/specs/2026-09-02-robopark-product-redesign-design.md`](../specs/2026-09-02-robopark-product-redesign-design.md)

**Required dependencies:** Plans [`01 Foundation`](2026-09-02-01-robopark-foundation.md), [`02 Operational Core`](2026-09-02-02-robopark-operational-core.md), and [`03 Reports and Analytics`](2026-09-02-03-robopark-reports-analytics.md) must be complete and green before Task 1. This preserves the shared `RouteManifest`/`AppRouter` history, makes the Phase 2 error classifier available to every frontend task, and guarantees that `/admin/tracker` can redirect to the registered `/work` route.

## Global Constraints

- Сохранять основной UX-порядок **«состояние → риск → следующее действие»** на auth/status/admin экранах.
- Все разрешённые функции должны работать на ПК и телефоне; списки управления на ширинах до 599 px становятся карточками, формы — последовательными, но возможности не исчезают.
- Использовать режимы Foundation без новых несовместимых breakpoints: compact phone до 599 px, phone/tablet 600–899 px, split tablet 900–1199 px, desktop от 1200 px.
- На 320 px не допускать горизонтального скролла основного layout; интерактивные цели — не меньше 44×44 px; mobile form controls — не меньше 16 px.
- Соблюдать WCAG AA, видимый focus не слабее 2 px, корректные labels, `aria-live` для успеха/загрузки и `role="alert"` для ошибок.
- Светлая, тёмная и системная темы используют один DOM и семантику Foundation; не создавать отдельные role/theme-компоненты.
- Domain CSS использует только Foundation tokens, включая `--rp-critical-surface`, `--rp-warning-surface`, `--rp-success-surface`, `--rp-info-surface`, `--rp-overlay`, `--rp-shadow-panel` и семантические spacing/color/radius tokens; не добавлять literal theme colors.
- Пользовательский термин — только **«Проверка робота»**. `Emergency` допускается лишь в технических API paths, Python/TypeScript identifiers и compatibility comments.
- Не добавлять логотипы, фирменные шрифты, геометрию или узнаваемые коммерческие силуэты Яндекса. Фотографии роботов не входят в этот план.
- Не читать и не выводить реальные `.env`, runtime DB, cookies, tokens, passwords, archives или пользовательские payload. Все тесты используют синтетические значения. Секрет может существовать только как временное local state/value в соответствующем password/secret input до отправки; он не рендерится как текст, не попадает в URL/storage/logs/audit/snapshot и очищается после attempt.
- Сохранять публичные backend URLs и response fields; новые поля — только additive. Не переименовывать технические `/admin/emergency/*` и `/admin/ops/*` endpoints.
- Backend остаётся источником истины для authorization. RouteManifest скрывает недоступное представление, но не заменяет server-side permission checks.
- Все административные mutations подтверждаются сервером до success UI. Optimistic update разрешён только для локального draft, не для сохранённого состояния.
- Долгоживущие admin-редакторы всегда отправляют полученный от API revision token. `409 stale_revision` сохраняет локальный draft, объясняет «Данные изменены другим пользователем» и предлагает сначала `Загрузить актуальные данные`; повторная отправка и повторное подтверждение никогда не выполняются автоматически.
- Использовать Foundation exports без изменения их signatures: `ROUTE_MANIFEST`, `canAccessRoute`, `landingPathForUser`, `navigationForUser`, `Icon`, `Button`, `IconButton`, `StatusBadge`, `PageLayout`, `Panel`, `FormField`, `LoadingState`, `EmptyState`, `ErrorState`, `StaleBadge`, `Dialog`, `BottomSheet`, `ConfirmDialog`, `useTheme`.
- `ConfirmDialog` остаётся controlled: parent задаёт `pending`/`error`, закрывает dialog только после успешной mutation; phrase validation выполняет сам primitive.
- Resource/admin API errors классифицировать через Phase 2 `apps/web/src/shared/api/classifyApiError.ts`; не создавать второй error classifier. Login/password credential failures сохраняют security-specific `mapLoginError`.
- URL хранит выбранную административную сущность и фильтры: `park`, `user`, `role`, `status`, `q`, `section`, `action`, `target`, `page`. URL никогда не содержит password/token/cookie/file path.
- Перед каждым коммитом выполнять `git diff --check` и `git diff --name-only`. После scoped `git add`, но до каждого `git commit`, обязательно выполнить `git diff --cached --check`, сверить `git diff --cached --name-only` с точным allowlist из `Files`/намеренных snapshots текущего task и остановиться при любом заранее staged, несвязанном, runtime, `.env`, database/WAL/SHM, upload, archive, log или Playwright-trace path; чужой index не очищать и не перезаписывать.
- Каждый task идёт RED → minimal GREEN → focused regression → отдельный commit; после каждого task приложение должно собираться.

### Canonical high-risk confirmation phrases

`confirmationPhrase` передаётся в Foundation `ConfirmDialog` буквально; UI не нормализует регистр, пробелы или динамический идентификатор. После `409` dialog остаётся открытым с ошибкой, но подтверждение сбрасывается и должно быть введено заново уже для новой revision. Поскольку primitive controlled, parent увеличивает локальный `conflictEpoch`; React `key` диалога составляется из action, base revision и conflict epoch. Это переинициализирует только phrase input, не draft и не выбранную сущность.

| Action | Exact `confirmationPhrase` |
| --- | --- |
| Деактивировать парк | `ДЕАКТИВИРОВАТЬ ${park.tag}` |
| Одобрить/отклонить заявку на парк | `ОДОБРИТЬ ${request.id}` / `ОТКЛОНИТЬ ${request.id}` |
| Одобрить/отклонить доступ пользователя | `ОДОБРИТЬ ${user.username}` / `ОТКЛОНИТЬ ${user.username}` |
| Деактивировать пользователя | `ДЕАКТИВИРОВАТЬ ${user.username}` |
| Понизить владельца | `ПОНИЗИТЬ ${user.username}` |
| Заменить пароль пользователя | `СБРОСИТЬ ПАРОЛЬ ${user.username}` |
| Удалить пользователя | `user.username` |
| Выдать пользователю хотя бы одно новое privileged permission | `ВЫДАТЬ ПРИВИЛЕГИИ ${user.username}` |
| Открыть/закрыть регистрацию | `ОТКРЫТЬ РЕГИСТРАЦИЮ` / `ЗАКРЫТЬ` |
| Выдать новой или существующей роли хотя бы одно новое privileged permission | `ВЫДАТЬ ПРИВИЛЕГИИ ${role.slug}` |
| Удалить роль | `role.slug` |
| Заменить OAuth-токен / cookie проверки | `ЗАМЕНИТЬ TRACKER` / `ЗАМЕНИТЬ ДОСТУП К ПРОВЕРКЕ` |
| Изменить Tracker policy / screenshot policy | `ИЗМЕНИТЬ ПОЛИТИКУ TRACKER` / `ИЗМЕНИТЬ ПОЛИТИКУ СКРИНШОТОВ` |
| Удалить раздел / поле проверки | `section.id` / `String(field.id)` |
| Создать snapshot / abort | `СОЗДАТЬ СНИМОК` / `ПРЕРВАТЬ` |
| Restore / update | phrase из текущего `OpsJob`; fallback `ВОССТАНОВИТЬ` / `ОБНОВИТЬ` |

---

## Foundation contracts consumed by every frontend task

```ts
// apps/web/src/app/routing/routeManifest.ts (created by Phase 1)
export type UserRole = 'royal' | 'admin' | 'operator' | 'mechanic' | 'driver'
export type NavGroup = 'operations' | 'collaboration' | 'insights' | 'administration'
export type NavSurface = 'desktop' | 'mobile'
export type AccessPrerequisite = 'password-changed' | 'approved' | 'mechanic-has-park'
export type RouteNav = {
  group: NavGroup
  desktopOrder: number
  mobilePriority?: Partial<Record<UserRole, number>>
}
export type RouteManifestItem = {
  id: AppRouteId
  path: string
  legacyPaths?: readonly string[]
  label: string
  icon: IconName
  permission?: string
  prerequisites?: readonly AccessPrerequisite[]
  surface: 'public' | 'standalone' | 'shell'
  nav?: RouteNav
}
export type NavigationItem = Pick<RouteManifestItem, 'id' | 'path' | 'label' | 'icon'> & {
  group: NavGroup
  priority: number
}
```

```ts
// apps/web/src/app/park/parkScope.ts (created by Phase 1)
export const PARK_QUERY_KEY = 'park'
export type ParkScopeValue = {
  parkId: number | null
  selectedPark: Park | null
  parks: Park[]
  loading: boolean
  locked: boolean
  setParkId(id: number, options?: { replace?: boolean }): void
  refreshParks(): Promise<void>
}
export function useParkScope(): ParkScopeValue
```

```ts
// apps/web/src/design-system/theme/ThemeProvider.tsx (created by Phase 1)
export type DensityPreference = 'comfortable' | 'compact'
export function useTheme(): {
  preference: ThemePreference
  resolvedTheme: 'light' | 'dark'
  setPreference(preference: ThemePreference): void
  densityPreference: DensityPreference
  resolvedDensity: DensityPreference
  setDensityPreference(preference: DensityPreference): void
}
```

```ts
// apps/web/src/design-system/overlays/ConfirmDialog.tsx (created by Phase 1)
export type ConfirmDialogProps = {
  open: boolean
  onOpenChange(open: boolean): void
  title: string
  description: string
  confirmLabel: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
  confirmationPhrase?: string
  pending?: boolean
  error?: string | null
  onConfirm(): void | Promise<void>
}
```

`apps/web/src/app/routing/AppRouter.tsx` owns `ROUTE_ELEMENTS: Record<AppRouteId, ReactElement>`. Every route added below must be added to both `AppRouteId`/`ROUTE_MANIFEST` and `ROUTE_ELEMENTS`; no second route table is allowed.

Reuse Foundation IDs `login`, `register`, `change-password`, `access-pending`, `access-rejected`, `mechanic-no-park`, `no-cabinet`, `admin`, `admin-tracker`, and `admin-robot-check`. Extend `AppRouteId` only with `account`, `admin-parks`, `admin-access`, `admin-roles`, `admin-integrations`, `admin-safety`, and `admin-system`; do not rename an existing ID while replacing its element or canonical path.

## Delivery map

| Task | Independently testable deliverable |
| --- | --- |
| 1 | Canonical admin capability gates match modular routes |
| 2 | Atomic, conflict-aware and secret-safe administrative writes/audit |
| 3 | Responsive login/register/change-password domain |
| 4 | Pending/rejected/no-park/no-cabinet/account journeys |
| 5 | `/admin/parks` with request queue and responsive editors |
| 6 | `/admin/access` with users, approvals and registration gate |
| 7 | `/admin/roles` with safe permission editing |
| 8 | `/admin/integrations` with redacted secret status |
| 9 | `/admin/safety` with screenshot policy and audit browser |
| 10 | `/admin/robot-check` configuration |
| 11 | `/admin/system` high-risk operations |
| 12 | Legacy redirects, cross-role E2E, visual/a11y gates and old admin cleanup |

---

### Task 1: Align backend capabilities with canonical administration modules

**Files:**

- Create: `apps/api/tests/test_admin_module_permissions.py`
- Modify: `apps/api/src/robopark_api/deps.py`
- Modify: `apps/api/src/robopark_api/routers/parks.py`
- Modify: `apps/api/src/robopark_api/routers/admin_roles.py`
- Modify: `apps/api/src/robopark_api/routers/admin_park_requests.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`
- Modify: `apps/api/src/robopark_api/services/rbac.py`
- Modify: `apps/api/src/robopark_api/services/rbac_seed.py`
- Modify: `apps/api/tests/test_rbac_seed.py`
- Modify: `apps/api/tests/test_ops_http.py`

**Interfaces:**

- Consumes: current `rbac.has_permission(db, user, permission) -> bool`, `rbac.assert_approved(user) -> None`, `require_user`.
- Produces: `require_any_permission(*permissions: str) -> Callable`; privileged permission constant `PERMISSION_SYSTEM_MANAGE = "system.manage"` assigned only to the royal system role by default, but enforceable for a deliberately granted custom role.
- Route mapping used later: parks → `parks.manage`, access → `users.manage`, roles → `roles.manage`, integrations/safety/robot-check → `nav.admin`, system → `system.manage`.

- [ ] **Step 1: Write focused capability tests**

Create `apps/api/tests/test_admin_module_permissions.py` with a synthetic custom role helper and these assertions:

```python
from sqlalchemy import select

from conftest import login_as
from robopark_api.models import AccessStatus, Permission, Role, User
from robopark_api.security import hash_password
from robopark_api.services import rbac


def add_capability_user(db_session, username: str, keys: set[str]) -> User:
    role = Role(
        slug=f"{username}_role",
        name=username,
        description="test role",
        is_system=False,
        is_active=True,
    )
    permissions = list(
        db_session.scalars(select(Permission).where(Permission.key.in_(keys))).all()
    )
    role.permissions = permissions
    db_session.add(role)
    db_session.flush()
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role.id,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_parks_manager_can_use_park_module_without_nav_admin(client, db_session, seed_royal):
    add_capability_user(db_session, "parks-only", {rbac.PERMISSION_PARKS_MANAGE})
    login_as(client, "parks-only", "secret")

    assert client.get("/parks").status_code == 200
    assert client.get("/admin/park-requests").status_code == 200
    assert client.post("/parks", json={"name": "North", "tag": "north"}).status_code == 201


def test_users_manager_can_read_parks_but_cannot_mutate_them(
    client, db_session, seed_royal
):
    add_capability_user(db_session, "users-only", {rbac.PERMISSION_USERS_MANAGE})
    login_as(client, "users-only", "secret")

    assert client.get("/parks").status_code == 200
    assert client.post("/parks", json={"name": "Denied", "tag": "denied"}).status_code == 403


def test_roles_manager_can_read_role_editor_dependencies_without_nav_admin(
    client, db_session, seed_royal
):
    add_capability_user(db_session, "roles-only", {rbac.PERMISSION_ROLES_MANAGE})
    login_as(client, "roles-only", "secret")

    assert client.get("/admin/roles").status_code == 200
    assert client.get("/admin/roles/permissions/catalog").status_code == 200


def test_system_manage_is_royal_only_in_seeded_defaults(client, seed_royal):
    login_as(client, "royal", "secret")
    roles = client.get("/admin/roles").json()
    by_slug = {role["slug"]: set(role["permissions"]) for role in roles}

    assert rbac.PERMISSION_SYSTEM_MANAGE in by_slug["royal"]
    assert rbac.PERMISSION_SYSTEM_MANAGE not in by_slug["admin"]


def test_explicit_system_manager_can_use_ops_without_royal_slug(
    client, db_session, seed_royal
):
    add_capability_user(db_session, "ops-only", {rbac.PERMISSION_SYSTEM_MANAGE})
    login_as(client, "ops-only", "secret")

    assert client.get("/admin/ops/job").status_code == 200
    assert client.post("/admin/ops/snapshot").status_code == 200
```

Keep the existing `test_admin_cannot_create_snapshot` regression in `test_ops_http.py`: the seeded admin has no `system.manage` and must still receive `403`. The custom-role case proves the route and API enforce the same capability instead of disagreeing by role slug.

- [ ] **Step 2: Run the permission module and confirm RED**

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_admin_module_permissions.py tests/test_rbac_seed.py
```

Expected: park/role custom-role tests return `403`, and `PERMISSION_SYSTEM_MANAGE` is missing.

- [ ] **Step 3: Add a reusable any-permission dependency**

Add to `apps/api/src/robopark_api/deps.py`:

```python
from collections.abc import Callable


def require_any_permission(*permissions: str) -> Callable[..., User]:
    required = frozenset(permissions)
    if not required:
        raise ValueError("at least one permission is required")

    def dependency(
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> User:
        rbac.assert_approved(user)
        if not any(rbac.has_permission(db, user, key) for key in required):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dependency
```

In `parks.py`, remove the router-wide `require_admin` dependency. Gate reads and writes separately:

```python
_require_parks_read = require_any_permission(
    rbac.PERMISSION_PARKS_MANAGE,
    rbac.PERMISSION_USERS_MANAGE,
    rbac.PERMISSION_NAV_ADMIN,
)


@router.get("", response_model=list[ParkOut])
def list_parks(
    db: Session = Depends(get_db),
    _actor: User = Depends(_require_parks_read),
) -> list[Park]:
    return list(db.scalars(select(Park).order_by(Park.id)).all())
```

Keep `_require_parks_manage` unchanged on `POST` and `PATCH`.

In `admin_roles.py`, use a read gate which also supports the user editor:

```python
_require_roles_read = require_any_permission(
    rbac.PERMISSION_ROLES_MANAGE,
    rbac.PERMISSION_USERS_MANAGE,
    rbac.PERMISSION_NAV_ADMIN,
)
```

Replace `Depends(require_admin)` on `list_permission_catalog` and `list_roles` with `Depends(_require_roles_read)`. Keep `roles.manage`/royal checks on create/update/delete.

In `admin_park_requests.py`, remove the router-wide `require_admin` dependency and gate list/approve/reject with:

```python
_require_park_requests = require_any_permission(
    rbac.PERMISSION_PARKS_MANAGE,
    rbac.PERMISSION_NAV_ADMIN,
)
```

Inject the returned actor into approve/reject; Task 2 uses that actor for audit attribution. A `users.manage`-only actor may read `/parks` for the user editor but must still receive `403` from `/admin/park-requests`.

In `admin_ops.py`, replace every `Depends(require_royal)` on `/admin/ops/job`, artifact, abort, snapshot, restore, and update with one shared dependency:

```python
_require_system_manage = require_permission(rbac.PERMISSION_SYSTEM_MANAGE)
```

Name the injected value `actor` and use that same user for existing/future audit records. Do not change the unauthenticated `/ops/maintenance` endpoint. `system.manage` remains in `PRIVILEGED_PERMISSIONS`, so only a royal actor may grant it through role administration; enforcement after an explicit grant is capability-based and therefore matches the manifest.

- [ ] **Step 4: Add the royal-only system capability**

In `rbac.py`, add the constant to `ALL_PERMISSIONS`, `PRIVILEGED_PERMISSIONS`, and `PERMISSION_CATALOG`:

```python
PERMISSION_SYSTEM_MANAGE = "system.manage"

PermissionDef(PERMISSION_SYSTEM_MANAGE, "action", "Системные операции", 340),
```

Change `DEFAULT_ROLE_PERMISSIONS` so royal receives it through `ALL_PERMISSIONS`, while admin excludes it:

```python
RoleSlug.ADMIN: frozenset(
    perm
    for perm in ALL_PERMISSIONS
    if perm not in {PERMISSION_USERS_APPROVE, PERMISSION_SYSTEM_MANAGE}
),
```

Extend `apps/api/tests/test_rbac_seed.py`:

```python
def test_system_manage_is_added_only_to_royal(db_session):
    ensure_rbac_catalog(db_session)
    roles = {
        role.slug: {permission.key for permission in role.permissions}
        for role in db_session.scalars(select(Role)).all()
    }
    assert "system.manage" in roles["royal"]
    assert "system.manage" not in roles["admin"]
```

- [ ] **Step 5: Run GREEN and capability regressions**

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q \
  tests/test_admin_module_permissions.py \
  tests/test_rbac_seed.py \
  tests/test_parks.py \
  tests/test_parks_extended.py \
  tests/test_admin_roles.py \
  tests/test_admin_user_access.py \
  tests/test_ops_http.py
uv run --frozen --extra dev ruff check src/robopark_api/deps.py src/robopark_api/routers/parks.py \
  src/robopark_api/routers/admin_roles.py \
  src/robopark_api/routers/admin_park_requests.py src/robopark_api/routers/admin_ops.py \
  src/robopark_api/services/rbac.py src/robopark_api/services/rbac_seed.py \
  tests/test_admin_module_permissions.py tests/test_ops_http.py
git diff --check
```

Expected: all listed tests pass; Ruff and diff check are clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/robopark_api/deps.py \
  apps/api/src/robopark_api/routers/parks.py \
  apps/api/src/robopark_api/routers/admin_roles.py \
  apps/api/src/robopark_api/routers/admin_park_requests.py \
  apps/api/src/robopark_api/routers/admin_ops.py \
  apps/api/src/robopark_api/services/rbac.py \
  apps/api/src/robopark_api/services/rbac_seed.py \
  apps/api/tests/test_admin_module_permissions.py \
  apps/api/tests/test_rbac_seed.py \
  apps/api/tests/test_ops_http.py
git commit -m "feat(api): align administration module capabilities"
```

---

### Task 2: Make administrative writes atomic, conflict-aware and visible in a secret-safe audit trail

**Files:**

- Create: `apps/api/tests/test_admin_audit_coverage.py`
- Create: `apps/api/tests/test_admin_revision_conflicts.py`
- Create: `apps/api/alembic/versions/0017_admin_revisions.py`
- Create: `apps/api/src/robopark_api/services/admin_mutations.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/services/audit.py`
- Modify: `apps/api/src/robopark_api/services/platform_settings.py`
- Modify: `apps/api/src/robopark_api/routers/parks.py`
- Modify: `apps/api/src/robopark_api/routers/operator_parks.py`
- Modify: `apps/api/src/robopark_api/routers/admin_park_requests.py`
- Modify: `apps/api/src/robopark_api/routers/admin_users.py`
- Modify: `apps/api/src/robopark_api/routers/admin_roles.py`
- Modify: `apps/api/src/robopark_api/routers/admin_settings.py`
- Modify: `apps/api/src/robopark_api/routers/admin_emergency.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`
- Modify: `apps/api/tests/test_models_migration.py`
- Modify: `apps/api/tests/test_audit.py`
- Modify: `apps/api/tests/test_platform_settings.py`
- Modify: `apps/api/tests/test_parks_extended.py`
- Modify: `apps/api/tests/test_park_requests.py`
- Modify: `apps/api/tests/test_admin_user_access.py`
- Modify: `apps/api/tests/test_access_requests.py`
- Modify: `apps/api/tests/test_admin_roles.py`
- Modify: `apps/api/tests/test_admin_emergency.py`
- Modify: `apps/api/tests/test_screenshot_guard_admin_settings.py`
- Modify: `apps/api/tests/test_tracker_policy_admin_settings.py`
- Modify: `apps/api/tests/test_registration_password_settings.py`
- Modify: `apps/api/tests/test_ops_http.py`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/api.adminRevision.test.ts`

**Interfaces:**

- Consumes: best-effort `audit.record(...)` for authentication/Tracker observations and the current `/admin/audit` page contract.
- Produces: `audit.add_entry(...) -> AuditLog`, which only attaches an entry to the caller's transaction and never commits or catches; canonical `audit.ACTION_ADMIN_*` constants; `audit.changed_fields_detail(fields: Iterable[str]) -> str`; audit `detail` contains field names/state only, never submitted values.
- Produces additive integer row versions in the database, opaque `revision: str` tokens in API responses, optional string `If-Match` on mutation endpoints, and frontend `AdminRevision = string`, `AdminPark = Park & { revision: AdminRevision }`, `AdminParkRequest = ParkRequest & { revision: AdminRevision }`. Shared Phase 1–3 `Park`/`ParkRequest` remain unchanged. Legacy callers may omit `If-Match`; every new Phase 4 editor must send it.
- Produces permanent monotonic settings revision groups `registration`, `integrations`, `tracker_policy`, and `screenshot_policy`; each accepted group mutation bumps its non-secret owner exactly once, and deleting/recreating ordinary value rows cannot reuse an older token.
- Produces a durable ops audit protocol: pending intent before filesystem side effects, attached job identity without early success, fresh-session terminal finalization, and idempotent GET/reconcile recovery.
- Later frontend consumes action strings only for labels and query filters; it never assumes an audit row was synchronously returned by a mutation.

- [ ] **Step 1: Write RED tests for catalog coverage and secret redaction**

Create `apps/api/tests/test_admin_audit_coverage.py`:

```python
import pytest

from conftest import login_as
from robopark_api.models import AuditLog, Park
from robopark_api.services import audit


EXPECTED_ADMIN_ACTIONS = {
    "admin.park.create",
    "admin.park.update",
    "admin.park_request.approve",
    "admin.park_request.reject",
    "admin.user.create",
    "admin.user.update",
    "admin.user.approve",
    "admin.user.reject",
    "admin.user.delete",
    "admin.role.create",
    "admin.role.update",
    "admin.role.delete",
    "admin.settings.tracker_token",
    "admin.settings.emergency_cookie",
    "admin.settings.tracker_policy",
    "admin.settings.screenshot_guard",
    "admin.settings.registration_password",
    "admin.integration.tracker_check",
    "admin.integration.robot_check_check",
    "admin.robot_check.section.create",
    "admin.robot_check.section.update",
    "admin.robot_check.section.reorder",
    "admin.robot_check.section.delete",
    "admin.robot_check.field.create",
    "admin.robot_check.field.update",
    "admin.robot_check.field.delete",
    "admin.ops.snapshot",
    "admin.ops.restore",
    "admin.ops.update",
    "admin.ops.abort",
}


def test_admin_audit_action_catalog_is_complete(client, seed_royal):
    login_as(client, "royal", "secret")
    actions = set(client.get("/admin/audit/actions").json())
    assert EXPECTED_ADMIN_ACTIONS <= actions


def test_secret_mutations_never_write_values_to_audit(
    client, seed_royal, db_session
):
    login_as(client, "royal", "secret")
    token = "synthetic-oauth-never-log"
    cookie = "Session_id=synthetic-never-log"

    assert client.put("/admin/settings/tracker-token", json={"token": token}).status_code == 200
    assert client.put("/admin/settings/emergency-cookie", json={"cookie": cookie}).status_code == 200

    rendered = "\n".join(row.detail or "" for row in db_session.query(AuditLog).all())
    assert token not in rendered
    assert cookie not in rendered


def test_changed_fields_detail_is_deterministic_and_value_free():
    assert audit.changed_fields_detail(["is_active", "name", "name"]) == (
        "changed=is_active,name"
    )


def test_admin_write_rolls_back_when_required_audit_cannot_be_added(
    client, seed_royal, db_session, monkeypatch
):
    login_as(client, "royal", "secret")

    def fail_required_audit(*_args, **_kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(audit, "add_entry", fail_required_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        client.post("/parks", json={"name": "Atomic", "tag": "atomic"})

    db_session.expire_all()
    assert db_session.query(Park).filter_by(tag="atomic").one_or_none() is None


def test_successful_admin_write_and_audit_commit_together(
    client, seed_royal, db_session
):
    login_as(client, "royal", "secret")
    response = client.post("/parks", json={"name": "Atomic", "tag": "atomic"})

    assert response.status_code == 201
    park = db_session.query(Park).filter_by(tag="atomic").one()
    entry = db_session.query(AuditLog).filter_by(
        action=audit.ACTION_ADMIN_PARK_CREATE,
        target_id=str(park.id),
    ).one()
    assert entry.actor_user_id == seed_royal.id
```

- [ ] **Step 2: Run the new tests and confirm RED**

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_admin_audit_coverage.py
```

Expected: missing constants/actions and `changed_fields_detail` cause failures; the failure-injection test proves the current post-commit `audit.record` lets a park survive an audit failure.

- [ ] **Step 3: Define one canonical action catalog and safe detail helper**

Add to `apps/api/src/robopark_api/services/audit.py`:

```python
from collections.abc import Iterable

ACTION_ADMIN_PARK_CREATE = "admin.park.create"
ACTION_ADMIN_PARK_UPDATE = "admin.park.update"
ACTION_ADMIN_PARK_REQUEST_APPROVE = "admin.park_request.approve"
ACTION_ADMIN_PARK_REQUEST_REJECT = "admin.park_request.reject"
ACTION_ADMIN_USER_CREATE = "admin.user.create"
ACTION_ADMIN_USER_UPDATE = "admin.user.update"
ACTION_ADMIN_USER_APPROVE = "admin.user.approve"
ACTION_ADMIN_USER_REJECT = "admin.user.reject"
ACTION_ADMIN_USER_DELETE = "admin.user.delete"
ACTION_ADMIN_ROLE_CREATE = "admin.role.create"
ACTION_ADMIN_ROLE_UPDATE = "admin.role.update"
ACTION_ADMIN_ROLE_DELETE = "admin.role.delete"
ACTION_ADMIN_TRACKER_POLICY = "admin.settings.tracker_policy"
ACTION_ADMIN_SCREENSHOT_GUARD = "admin.settings.screenshot_guard"
ACTION_ADMIN_REGISTRATION_PASSWORD = "admin.settings.registration_password"
ACTION_ADMIN_INTEGRATION_TRACKER_CHECK = "admin.integration.tracker_check"
ACTION_ADMIN_INTEGRATION_ROBOT_CHECK = "admin.integration.robot_check_check"
ACTION_ADMIN_ROBOT_CHECK_SECTION_CREATE = "admin.robot_check.section.create"
ACTION_ADMIN_ROBOT_CHECK_SECTION_UPDATE = "admin.robot_check.section.update"
ACTION_ADMIN_ROBOT_CHECK_SECTION_REORDER = "admin.robot_check.section.reorder"
ACTION_ADMIN_ROBOT_CHECK_SECTION_DELETE = "admin.robot_check.section.delete"
ACTION_ADMIN_ROBOT_CHECK_FIELD_CREATE = "admin.robot_check.field.create"
ACTION_ADMIN_ROBOT_CHECK_FIELD_UPDATE = "admin.robot_check.field.update"
ACTION_ADMIN_ROBOT_CHECK_FIELD_DELETE = "admin.robot_check.field.delete"
ACTION_ADMIN_OPS_SNAPSHOT = "admin.ops.snapshot"
ACTION_ADMIN_OPS_RESTORE = "admin.ops.restore"
ACTION_ADMIN_OPS_UPDATE = "admin.ops.update"
ACTION_ADMIN_OPS_ABORT = "admin.ops.abort"
OUTCOME_PENDING = "pending"


def changed_fields_detail(fields: Iterable[str]) -> str:
    names = sorted({str(field) for field in fields if str(field)})
    return f"changed={','.join(names)}" if names else "changed=none"
```

Keep the existing tracker-token and cookie constants unchanged. Replace generic `ACTION_SETTINGS_CHANGED` at registration/user writes with the specific constants above. Move the three local ops constants from `admin_ops.py` to `audit.py`; otherwise `/admin/audit/actions` cannot discover them.

Extract entry construction from `record` into this required, transaction-neutral primitive; keep every keyword and default aligned with `record`:

```python
def add_entry(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    actor_username: str | None = None,
    park_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = OUTCOME_SUCCESS,
    detail: str | None = None,
    client_ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        actor_user_id=actor.id if actor is not None else None,
        actor_username=actor.username if actor is not None else actor_username,
        actor_role=actor.role if actor is not None else None,
        park_id=park_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        detail=detail,
        client_ip=client_ip,
    )
    db.add(entry)
    return entry
```

`record` calls `add_entry`, commits inside its existing `try`, and retains its current best-effort/no-raise contract for auth and Tracker observations. Every DB-backed administrative mutation below instead calls `add_entry` before its sole `db.commit()`; it never calls `record`. An audit insert/flush/commit failure therefore rolls back the business row and audit row together.

- [ ] **Step 4: Record every successful mutation with an exact safe payload policy**

Apply this exhaustive mapping. `target_id` is always the database/string identifier; `detail` is exactly the value in the last column and never a request value.

| Endpoint | Action | target_type | detail |
| --- | --- | --- | --- |
| `POST /parks` | `ACTION_ADMIN_PARK_CREATE` | `park` | `changed_fields_detail(payload.model_fields_set)` |
| `PATCH /parks/{id}` | `ACTION_ADMIN_PARK_UPDATE` | `park` | `changed_fields_detail(changes)` |
| park request approve/reject | corresponding `PARK_REQUEST_*` | `park_request` | `changed=resolution` |
| user create | `ACTION_ADMIN_USER_CREATE` | `user` | `changed=account` |
| user update | `ACTION_ADMIN_USER_UPDATE` | `user` | `changed_fields_detail(changes)` |
| user approve/reject/delete | corresponding `USER_*` | `user` | `changed=access_status` or `changed=deleted` |
| role create/update/delete | corresponding `ROLE_*` | `role` | `changed=role`, `changed_fields_detail(changes)`, or `changed=deleted` |
| tracker token / robot-check cookie replacement | existing `ACTION_TRACKER_TOKEN_SET` / `ACTION_EMERGENCY_COOKIE_SET` | `settings` | `changed=configured` |
| tracker policy | `ACTION_ADMIN_TRACKER_POLICY` | `settings` | `changed_fields_detail(payload.model_fields_set)` |
| screenshot guard | `ACTION_ADMIN_SCREENSHOT_GUARD` | `settings` | `changed_fields_detail(payload.model_fields_set)` |
| registration password PUT/DELETE | `ACTION_ADMIN_REGISTRATION_PASSWORD` | `settings` | `changed=configured` |
| integration diagnostic checks | corresponding `INTEGRATION_*_CHECK` | `integration` | `changed=diagnostic` |
| robot-check section create/update/reorder/delete | corresponding section action | `robot_check_section` | `changed=section`, field names, `changed=order`, or `changed=deleted` |
| robot-check field create/update/delete | corresponding field action | `robot_check_field` | `changed=field`, field names, or `changed=deleted` |
| ops snapshot/restore/update/abort | corresponding ops action | `ops_job` | `intent_recorded` before the side effect; then normalized lifecycle state only |

Each router must inject the actor used by the audit record. Example for parks:

```python
def create_park(
    payload: ParkCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(_require_parks_manage),
) -> Park:
    if _tag_exists(db, payload.tag):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    with admin_transaction(db):
        park = Park(**payload.model_dump(), is_active=True)
        db.add(park)
        db.flush()
        audit.add_entry(
            db,
            action=audit.ACTION_ADMIN_PARK_CREATE,
            actor=actor,
            target_type="park",
            target_id=str(park.id),
            detail=audit.changed_fields_detail(payload.model_fields_set),
        )
    db.refresh(park)
    return park
```

For `admin_emergency.py`, keep the router permission dependency and additionally inject `actor: User = Depends(require_user)` in every mutation so the record has an actor. Replace `_commit_write` with `admin_transaction(db)` around each mutation plus `audit.add_entry`, and invalidate the config cache only after the context commits successfully. Do not put section titles, JSON meta, field paths or labels into audit detail.

`platform_settings.set_setting` currently commits too early. Change it and its boolean/registration wrappers additively:

```python
def set_setting(
    db: Session,
    key: str,
    value: str,
    *,
    commit: bool = True,
) -> PlatformSetting:
    stored = encrypt_secret(value, _secret_key()) if key in SECRET_KEYS else value
    row = db.get(PlatformSetting, key)
    now = datetime.now(UTC)
    if row is None:
        row = PlatformSetting(key=key, value=stored, updated_at=now)
        db.add(row)
    else:
        row.value = stored
        row.updated_at = now
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row
```

Define the permanent group-owner protocol in `platform_settings.py`; settings revisions are no longer hashes of the current value rows:

```python
from typing import Literal

from sqlalchemy.exc import IntegrityError

SettingsRevisionGroup = Literal[
    "registration", "integrations", "tracker_policy", "screenshot_policy"
]
SETTINGS_REVISION_OWNER_KEYS: dict[SettingsRevisionGroup, str] = {
    "registration": "__revision__.registration",
    "integrations": "__revision__.integrations",
    "tracker_policy": "__revision__.tracker_policy",
    "screenshot_policy": "__revision__.screenshot_policy",
}


class SettingsRevisionRace(RuntimeError):
    pass


def settings_revision(db: Session, group: SettingsRevisionGroup) -> str:
    owner = db.get(PlatformSetting, SETTINGS_REVISION_OWNER_KEYS[group])
    return owner.value if owner is not None else "0"


def bump_settings_revision(db: Session, group: SettingsRevisionGroup) -> str:
    key = SETTINGS_REVISION_OWNER_KEYS[group]
    owner = db.get(PlatformSetting, key)
    creating = owner is None
    if owner is None:  # Compatibility with a legacy DB not yet owning this group.
        owner = PlatformSetting(key=key, value="1", updated_at=datetime.now(UTC))
        db.add(owner)
    else:
        try:
            current = int(owner.value)
        except ValueError as exc:
            raise RuntimeError("invalid settings revision owner") from exc
        if current < 0:
            raise RuntimeError("invalid settings revision owner")
        owner.value = str(current + 1)
        owner.updated_at = datetime.now(UTC)
    try:
        db.flush()
    except IntegrityError as exc:
        if not creating:
            raise
        raise SettingsRevisionRace from exc
    return owner.value
```

`delete_setting(db: Session, key: str, *, commit: bool = True) -> None` deletes an ordinary value row when present and commits only when requested, but rejects every key in `SETTINGS_REVISION_OWNER_KEYS.values()`; no public helper or reset path may delete/recreate a group owner. Add `commit: bool = True` as keyword-only to `set_bool_setting`, `set_emergency_cookie_valid`, `set_registration_shared_password`, and `clear_registration_shared_password`; each forwards it and preserves current behavior for non-admin/background callers. These leaf setters and `delete_setting` never bump a group themselves.

Every admin settings mutation follows one endpoint-level protocol inside `admin_transaction`: read `settings_revision(db, group)`, validate `If-Match` before any probe/write, apply all ordinary row changes with `commit=False`, call `bump_settings_revision(db, group)` exactly once even for a semantic no-op, attach exactly one required audit entry, and commit once. The response is serialized only after commit and exposes the bumped group token. A missing legacy owner serializes as `"0"`; its first accepted mutation creates counter `"1"`. If two legacy requests race to create the same missing owner, the losing owner-key insert is normalized to `409 stale_revision`, not a generic integrity error. Cache invalidation happens only after commit; Task 8 relocates report resolution from cookie replacement to a verified successful diagnostic. Tracker-token/cookie/diagnostic writes share `integrations`; multi-key Tracker and screenshot policies use `tracker_policy` and `screenshot_policy`; registration PUT/DELETE use `registration`. Deleting ordinary rows therefore cannot restore any earlier token, and delete/recreate remains conflict-safe.

Filesystem-backed ops cannot share the SQL transaction. Preserve that boundary honestly with `_record_ops_intent(db: Session, actor: User, action: str) -> AuditLog`, `_attach_ops_job(db: Session, entry_id: int, job: OpsJob) -> None`, and an idempotent `_finalize_ops_audit(db: Session, entry_id: int, job: OpsJob) -> None`. The first helper creates one required row with `outcome=OUTCOME_PENDING` and `detail="intent_recorded"`, commits it, and only then may the router call `begin_job`/`start_and_run`/`abort_job`; if the first commit fails, do not touch the ops directory. After begin succeeds, `_attach_ops_job` sets `target_type="ops_job"`, `target_id=job.id`, and safe `detail=job.state` on that same row but deliberately keeps `outcome=OUTCOME_PENDING`. A queued/running job is never success.

For `ops_sync=False`, schedule a wrapper around `execute_job` only after the attachment commit. The wrapper receives the audit entry ID, executes the job, reloads its final disk state in `finally`, opens a **fresh `SessionLocal()`**, and finalizes that same row only when the job is terminal: `succeeded` maps to `OUTCOME_SUCCESS`; `failed` or `aborted` maps to `OUTCOME_FAILURE`. It never reuses the closed request session. `_finalize_ops_audit` selects by audit ID, verifies the stored target job ID, updates only a still-pending row, and is therefore idempotent. `GET /admin/ops/job` and the existing ops reconcile path call the same idempotent reconciliation after loading disk state, so a restart or failed background finalizer eventually closes a terminal pending row. They leave queued/running rows pending. `ops_sync=True` may attach and immediately finalize only when `start_and_run` has already returned a terminal job.

Launch rejection after intent finalizes failure with exactly one fixed detail (`launch_failed` or `job_in_progress`). Abort records its own pending intent before calling `abort_job`; `no_active_job` finalizes that abort row as failure, while a completed abort finalizes the abort action as success only after the filesystem state has been changed. The aborted original snapshot/restore/update intent is reconciled separately as failure. If the process crashes or a final audit update fails after the side effect begins, the durable row remains pending, never a fabricated success. Never put `job.error`, uploaded filename/archive bytes, token hash, command output, log content, or arbitrary exception text in audit; only `intent_recorded`, normalized job states, and the fixed rejection details above are allowed. Tests must call this a durable audit-before-side-effect protocol, not cross-storage atomicity.

- [ ] **Step 5: Extend behavioral audit assertions in existing router tests**

Add assertions after successful mutations in the closest tests. Example for a robot-check delete in `test_admin_emergency.py`:

```python
entry = db_session.query(AuditLog).filter_by(
    action=audit.ACTION_ADMIN_ROBOT_CHECK_SECTION_DELETE,
    target_id="batteries",
).one()
assert entry.actor_user_id == seed_royal.id
assert entry.detail == "changed=deleted"
```

Add equivalent exact assertions to:

- `test_parks_extended.py` for park create/update;
- `test_park_requests.py` for approve/reject;
- `test_admin_user_access.py` for create/update/delete;
- `test_access_requests.py` for approve/reject;
- `test_admin_roles.py` for create/update/delete;
- `test_screenshot_guard_admin_settings.py` and `test_tracker_policy_admin_settings.py` for setting changes;
- `test_registration_password_settings.py` for PUT/DELETE;
- `test_ops_http.py` for snapshot and abort.

In `test_ops_http.py`, monkeypatch begin/launch to raise after the intent commit and separately monkeypatch the attach/finalize path to fail after a synthetic job is created. Before attachment, the matching single `AuditLog` remains `OUTCOME_PENDING` with `detail == "intent_recorded"`; after attachment failure it may additionally have the safe job ID/state, but it must still be pending. No test may interpret either state as success.

Add an `ops_sync=False` regression with a controlled background executor: return a queued/running synthetic job, inspect the audit row before allowing the worker to finish, and assert it is still the same `OUTCOME_PENDING` row with the job ID attached. Then finish it as `succeeded` and assert the fresh-session wrapper changes that row to success without appending another row. Repeat the terminal mapping for `failed`/aborted as failure, simulate the wrapper finalizer raising, and prove a later `GET /admin/ops/job`/reconcile finalizes the orphaned pending row idempotently. The synchronous terminal tests may expect immediate finalization. Abort coverage asserts its own audit row is not success before `abort_job` completes, is success afterward, and contains neither `job.error` nor arbitrary exception text. The normal snapshot/restore/update/abort cases always assert the same row is finalized rather than a second row being appended.

Every assertion compares only action, actor, target, outcome and safe detail; test credentials must never be interpolated into a failure message.

- [ ] **Step 6: Add backward-compatible optimistic concurrency tokens**

Create `0017_admin_revisions.py` after the Phase 3 migration head `0016_report_idempotency`:

```python
"""Optimistic revisions for administrative resources.

Revision ID: 0017_admin_revisions
Revises: 0016_report_idempotency
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_admin_revisions"
down_revision: str | None = "0016_report_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVISIONED_TABLES = (
    "parks",
    "users",
    "roles",
    "park_requests",
    "platform_settings",
    "emergency_sections",
    "emergency_fields",
)

SETTINGS_REVISION_OWNER_KEYS = (
    "__revision__.registration",
    "__revision__.integrations",
    "__revision__.tracker_policy",
    "__revision__.screenshot_policy",
)

platform_settings = sa.table(
    "platform_settings",
    sa.column("key", sa.String(length=64)),
    sa.column("value", sa.Text()),
)


def upgrade() -> None:
    for table_name in REVISIONED_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "revision",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
    op.bulk_insert(
        platform_settings,
        [{"key": key, "value": "0"} for key in SETTINGS_REVISION_OWNER_KEYS],
    )


def downgrade() -> None:
    op.execute(
        platform_settings.delete().where(
            platform_settings.c.key.in_(SETTINGS_REVISION_OWNER_KEYS)
        )
    )
    for table_name in reversed(REVISIONED_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("revision")
```

Add `revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))` and `__mapper_args__ = {"version_id_col": revision}` to `Park`, `User`, `Role`, `ParkRequest`, `PlatformSetting`, `EmergencySection`, and `EmergencyField`. SQLAlchemy then rejects a write that raced after the server read. For relationship-only updates (`UserPark`, `UserPermission`, `RolePermission`, `EmergencySectionRole`, field create/delete, and section reorder), increment the owning row's `revision` explicitly before commit. The four seeded `PlatformSetting` rows above are permanent, non-secret group revision owners; their decimal `value` is the external group counter and is independent of any ordinary setting row's lifecycle.

Create `services/admin_mutations.py`:

```python
from collections.abc import Generator
from contextlib import contextmanager
from hmac import compare_digest

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from robopark_api.services.platform_settings import SettingsRevisionRace

STALE_REVISION_DETAIL = "stale_revision"


def revision_token(revision: int) -> str:
    return str(revision)


def require_revision(expected: str | None, current: str) -> None:
    if expected is not None and not compare_digest(expected, current):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=STALE_REVISION_DETAIL,
        )


@contextmanager
def admin_transaction(db: Session) -> Generator[None, None, None]:
    try:
        yield
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=STALE_REVISION_DETAIL,
        ) from exc
    except SettingsRevisionRace as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=STALE_REVISION_DETAIL,
        ) from exc
    except Exception:
        db.rollback()
        raise
```

All response tokens are opaque strings even though row versions are integers. In `schemas.py`, add a reusable response base so ORM-returning legacy routes also serialize safely:

```python
class RevisionedOut(BaseModel):
    revision: str

    @field_validator("revision", mode="before")
    @classmethod
    def stringify_revision(cls, value: object) -> str:
        return str(value)
```

Make `ParkOut`, `EmergencySectionAdminOut`, and `EmergencyFieldAdminOut` inherit it; import it for `UserAdminOut`, `RoleOut`, and `ParkRequestOut`. Add the same required `revision: str` to settings outputs. Explicit entity serializers call `revision_token(row.revision)`; ORM response validation goes through `RevisionedOut`. Settings serializers call `platform_settings.settings_revision(db, group)` with exactly one of the four canonical group names; they never derive a token from owned keys, row presence, timestamps, revisions, or setting values. Existing response JSON only gains a string field; no old field changes type.

In `apps/web/src/api.ts`, do not add a required field to shared Phase 1–3 DTOs:

```ts
export type AdminRevision = string
export type AdminPark = Park & { revision: AdminRevision }
export type AdminParkRequest = ParkRequest & { revision: AdminRevision }
```

Keep `api.parks(): Promise<Park[]>` and `api.operatorParkRequests(): Promise<ParkRequest[]>` unchanged. Add `api.adminParks(): Promise<AdminPark[]>` over the same `GET /parks` URL; type only `api.adminParkRequests()` as `Promise<AdminParkRequest[]>`. Admin create/update methods return `AdminPark`, while public/Foundation callers continue to compile with ordinary `Park` fixtures.

Every DB-backed POST/PATCH/PUT/DELETE/approve/reject endpoint performs its business change and `audit.add_entry` inside `admin_transaction`. Every update/delete additionally reads optional `if_match: Annotated[str | None, Header(alias="If-Match")] = None` and compares it before mutation. Omission keeps older clients working. The new web client exports `type AdminRevision = string` and always supplies `{ 'If-Match': revision }` for:

| API method | Revision source |
| --- | --- |
| `updatePark`, park request approve/reject | selected `AdminPark.revision` / `AdminParkRequest.revision` |
| `updateAdminUser`, approve/reject/delete user | `AdminUser.revision` |
| `updateAdminRole`, `deleteAdminRole` | `AdminRole.revision` |
| token/cookie, Tracker policy, screenshot policy, registration password PUT/DELETE | corresponding settings response `revision` |
| section update/delete and field create | section `revision` |
| field update/delete | field `revision` |
| section reorder | `expected_revisions: Record<string, AdminRevision>` added to the existing JSON body next to `ids` |

Create operations have no prior revision. For section reorder, backend validates every `expected_revisions[id]` against every current section before changing any order; an omitted map remains the legacy behavior. Do not use revision as authorization, expose it in URL, or include it in audit `detail`.

- [ ] **Step 7: Prove stale clients cannot overwrite newer state**

Create `test_admin_revision_conflicts.py` with an HTTP-level stale-client test:

```python
from conftest import login_as


def test_stale_park_revision_returns_recoverable_conflict(
    client, seed_royal, seed_park_with_tracker, db_session
):
    login_as(client, "royal", "secret")
    initial = client.get("/parks").json()[0]

    first = client.patch(
        f"/parks/{seed_park_with_tracker.id}",
        headers={"If-Match": initial["revision"]},
        json={"name": "First saved name"},
    )
    stale = client.patch(
        f"/parks/{seed_park_with_tracker.id}",
        headers={"If-Match": initial["revision"]},
        json={"name": "Stale overwrite"},
    )

    assert first.status_code == 200
    assert first.json()["revision"] != initial["revision"]
    assert stale.status_code == 409
    assert stale.json()["detail"] == "stale_revision"
    db_session.refresh(seed_park_with_tracker)
    assert seed_park_with_tracker.name == "First saved name"
```

Add a registration-group ABA regression to `test_registration_password_settings.py`: configure a synthetic password and save its original revision, DELETE with that token, recreate with the DELETE response revision, then attempt another PUT with the original pre-delete token. Assert the delete and recreate each advance the token, the last request returns `409 stale_revision`, and the recreated password remains effective. In the settings router suites, parameterize representative mutations for all four groups and assert each accepted endpoint call advances its owner counter by exactly one regardless of how many ordinary rows it changes, while a stale/rejected/rolled-back mutation does not advance it.

Add the same delete/recreate assertion at the service level in `test_platform_settings.py`, including `settings_revision(db, group) == "0"` for a missing legacy owner, first bump to `"1"`, and rejection of `delete_setting` for every reserved owner key. These tests must prove no setting value or secret participates in the token.

Add the same old-token/new-token/persisted-first-write assertion to the closest router test for users, roles, park-request resolution, Tracker policy, screenshot policy, section update, field update, and reorder. Extend `test_models_migration.py` to upgrade a database at `0016_report_idempotency` through `0017_admin_revisions`, assert all seven tables receive non-null row revision `1`, and assert the four permanent group owners exist with counter value `"0"`. These tests use two sequential requests carrying the same original token: that exactly models two browser editors without timing-dependent threads.

Create `apps/web/src/api.adminRevision.test.ts` as a compile-time compatibility regression:

```ts
import { expectTypeOf, it } from 'vitest'
import type { AdminPark, AdminParkRequest, Park, ParkRequest } from './api'

it('keeps shared fixtures revision-free and requires tokens in admin resources', () => {
  const sharedPark: Park = { id: 7, name: 'Север', tag: 'north' }
  const sharedRequest: ParkRequest = {
    id: 12, user_id: 3, park_id: 7, status: 'pending',
    created_at: '2026-09-02T10:00:00Z', resolved_at: null, resolved_by: null,
  }
  expectTypeOf(sharedPark).toMatchTypeOf<Park>()
  expectTypeOf(sharedRequest).toMatchTypeOf<ParkRequest>()
  expectTypeOf<AdminPark['revision']>().toEqualTypeOf<string>()
  expectTypeOf<AdminParkRequest['revision']>().toEqualTypeOf<string>()
})
```

- [ ] **Step 8: Run GREEN and audit/concurrency regressions**

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q \
  tests/test_admin_audit_coverage.py \
  tests/test_admin_revision_conflicts.py \
  tests/test_audit.py \
  tests/test_parks_extended.py \
  tests/test_park_requests.py \
  tests/test_admin_user_access.py \
  tests/test_access_requests.py \
  tests/test_admin_roles.py \
  tests/test_admin_emergency.py \
  tests/test_screenshot_guard_admin_settings.py \
  tests/test_tracker_policy_admin_settings.py \
  tests/test_registration_password_settings.py \
  tests/test_ops_http.py \
  tests/test_models_migration.py \
  tests/test_platform_settings.py
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
git diff --check
cd ../web
npm test -- src/api.adminRevision.test.ts
npm run build
cd ../..
```

Expected: all focused tests pass; action catalog includes every table row; audit failure rolls back a DB-backed mutation; every stale token, including the original settings token after delete/recreate, returns `409 stale_revision`; async queued/running ops audit remains pending until terminal finalization; no assertion finds a synthetic secret or raw job error.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/robopark_api/services/audit.py \
  apps/api/src/robopark_api/services/admin_mutations.py \
  apps/api/src/robopark_api/services/platform_settings.py \
  apps/api/src/robopark_api/models.py \
  apps/api/src/robopark_api/schemas.py \
  apps/api/alembic/versions/0017_admin_revisions.py \
  apps/api/src/robopark_api/routers/parks.py \
  apps/api/src/robopark_api/routers/operator_parks.py \
  apps/api/src/robopark_api/routers/admin_park_requests.py \
  apps/api/src/robopark_api/routers/admin_users.py \
  apps/api/src/robopark_api/routers/admin_roles.py \
  apps/api/src/robopark_api/routers/admin_settings.py \
  apps/api/src/robopark_api/routers/admin_emergency.py \
  apps/api/src/robopark_api/routers/admin_ops.py \
  apps/api/tests/test_admin_audit_coverage.py \
  apps/api/tests/test_admin_revision_conflicts.py \
  apps/api/tests/test_audit.py \
  apps/api/tests/test_parks_extended.py \
  apps/api/tests/test_park_requests.py \
  apps/api/tests/test_admin_user_access.py \
  apps/api/tests/test_access_requests.py \
  apps/api/tests/test_admin_roles.py \
  apps/api/tests/test_admin_emergency.py \
  apps/api/tests/test_screenshot_guard_admin_settings.py \
  apps/api/tests/test_tracker_policy_admin_settings.py \
  apps/api/tests/test_registration_password_settings.py \
  apps/api/tests/test_ops_http.py \
  apps/api/tests/test_models_migration.py \
  apps/api/tests/test_platform_settings.py \
  apps/web/src/api.ts \
  apps/web/src/api.adminRevision.test.ts
git commit -m "feat(api): make admin mutations auditable and conflict-aware"
```

---

### Task 3: Rebuild login, registration and forced password change

**Files:**

- Create: `apps/web/src/domains/onboarding/components/AuthLayout.tsx`
- Create: `apps/web/src/domains/onboarding/components/AuthLayout.css`
- Create: `apps/web/src/domains/onboarding/components/PasswordField.tsx`
- Create: `apps/web/src/domains/onboarding/components/PasswordRequirements.tsx`
- Create: `apps/web/src/domains/onboarding/components/RolePicker.tsx`
- Create: `apps/web/src/domains/onboarding/authCopy.ts`
- Create: `apps/web/src/domains/onboarding/pages/LoginPage.tsx`
- Create: `apps/web/src/domains/onboarding/pages/RegisterPage.tsx`
- Create: `apps/web/src/domains/onboarding/pages/ChangePasswordPage.tsx`
- Create: `apps/web/src/domains/onboarding/onboarding.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/i18n/ru.ts`
- Modify: `apps/web/src/i18n/errors.ts`
- Modify: `apps/web/src/i18n/errors.test.ts`
- Delete: `apps/web/src/pages/Login.tsx`
- Delete: `apps/web/src/pages/Register.tsx`
- Delete: `apps/web/src/pages/ChangePassword.tsx`

**Interfaces:**

- Consumes: `AuthContextValue.login`, `refreshUser`, `logout`; `api.register`, `api.changePassword`; Phase 2 `classifyApiError`; Foundation `FormField`, `Button`, `StatusBadge`, `Icon`.
- Produces: `AUTH_HEADING`, `AuthLayout`, `PasswordField`, `PasswordRequirements`, `RolePicker`; route components `LoginPage`, `RegisterPage`, `ChangePasswordPage`.
- Keeps current `LAST_USERNAME_KEY = "robopark.lastUsername"`; password/shared password are React state only and are never persisted.

- [ ] **Step 1: Write component journeys first**

Create `onboarding.test.tsx`. Use `MemoryRouter`, `AuthContext.Provider`, `fireEvent`, `screen`, `waitFor`, and `vi.spyOn(api, ...)`. Cover these exact behaviors:

```tsx
it.each([
  { page: <LoginPage />, heading: AUTH_HEADING.login, user: null },
  { page: <RegisterPage />, heading: AUTH_HEADING.register, user: null },
  { page: <ChangePasswordPage />, heading: AUTH_HEADING.changePassword, user: forcedUser },
] as const)('renders one exact auth h1 for $heading', ({ page, heading, user }) => {
  renderOnboarding(page, user)
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
})

it('describes all self-registration roles with the approved product term', () => {
  renderOnboarding(<RegisterPage />, null)
  expect(screen.getByRole('radio', { name: /Оператор/ })).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: /Механик/ })).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: /Водитель/ })).toBeInTheDocument()
  expect(screen.getByText(/Проверка робота/)).toBeInTheDocument()
  expect(screen.queryByText(/Emergency/)).not.toBeInTheDocument()
})

it('shows password requirements before registration is submitted', () => {
  renderOnboarding(<RegisterPage />, null)
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'RoboparkPass1' } })
  expect(screen.getByText('Не менее 12 символов')).toHaveAttribute('data-met', 'true')
  expect(screen.getByText('Не менее трёх типов знаков')).toHaveAttribute('data-met', 'true')
  expect(screen.getByRole('button', { name: 'Зарегистрироваться' })).toBeEnabled()
})

it('keeps forced password change blocking and routes only after refresh', async () => {
  const refreshUser = vi.fn().mockResolvedValue({ ...forcedUser, must_change_password: false })
  vi.spyOn(api, 'changePassword').mockResolvedValue(undefined)
  renderOnboarding(<ChangePasswordPage />, forcedUser, { refreshUser })
  fillPasswordChangeForm('CurrentPass1!', 'NewRoboparkPass1!', 'NewRoboparkPass1!')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить пароль' }))
  await waitFor(() => expect(api.changePassword).toHaveBeenCalledWith(
    'CurrentPass1!',
    'NewRoboparkPass1!',
  ))
  expect(refreshUser).toHaveBeenCalledOnce()
})
```

Add error-classification journeys. Login `ApiError(401)` renders only `Неверный логин или пароль`; login `ApiTimeoutError`, offline `TypeError`, and `ApiError(503, ..., 'req-auth-7')` render the Phase 2 timeout/offline/server title, retry where permitted, and request ID, never the bad-credentials message. Forced password change maps only its own `401` to `Текущий пароль не принят`; policy detail errors retain their safe `mapApiError` description, while timeout/offline/5xx use the operational classifier and keep the form retryable. Assert password fields clear after success and non-retryable credential/policy failures. For a retryable transport failure, values may remain only in mounted React state (never storage); click `Повторить`, prove the same request runs once more, then clear them after success. Username/role and non-secret form context always remain.

Define `forcedUser`, `renderOnboarding`, and `fillPasswordChangeForm` in the same test file with complete `User`/`AuthContextValue` fixtures. Use only synthetic passwords shown above.

- [ ] **Step 2: Run RED**

Run:

```bash
cd apps/web
npm test -- src/domains/onboarding/onboarding.test.tsx
```

Expected: FAIL because the new domain components do not exist.

- [ ] **Step 3: Build the shared auth layout and form primitives**

Create `authCopy.ts`; pages and Playwright import these values instead of repeating headings:

```ts
export const AUTH_HEADING = {
  login: 'Вход',
  register: 'Регистрация',
  changePassword: 'Смена пароля',
} as const
```

Implement `AuthLayout` with one landmark, an original CSS robot-grid mark, title/subtitle, content and optional footer:

```tsx
import type { ReactNode } from 'react'
import { Icon } from '../../../design-system/icons/Icon'
import './AuthLayout.css'

export type AuthLayoutProps = {
  title: string
  subtitle: string
  children: ReactNode
  footer?: ReactNode
}

export function AuthLayout({ title, subtitle, children, footer }: AuthLayoutProps) {
  return (
    <main className="auth-layout">
      <section aria-labelledby="auth-title" className="auth-card">
        <div aria-hidden="true" className="auth-mark"><Icon name="robot" /></div>
        <h1 id="auth-title">{title}</h1>
        <p className="auth-subtitle">{subtitle}</p>
        {children}
        {footer ? <footer className="auth-footer">{footer}</footer> : null}
      </section>
    </main>
  )
}
```

`PasswordField` must keep its value controlled, use `FormField`, set mobile-safe input attributes, and expose a text toggle with a 44 px target. Its public type is:

```ts
export type PasswordFieldProps = {
  id: string
  label: string
  value: string
  onChange(value: string): void
  hint?: string
  error?: string
  autoComplete: 'current-password' | 'new-password' | 'off'
  required?: boolean
  autoFocus?: boolean
  disabled?: boolean
}
```

Implement `PasswordRequirements` from the existing `passwordChecks` function:

```tsx
export function PasswordRequirements({ password }: { password: string }) {
  const checks = passwordChecks(password)
  return (
    <div aria-live="polite" className="password-requirements">
      <p>Надёжность пароля: {checks.ok ? 'достаточная' : 'недостаточная'}</p>
      <ul>
        <li data-met={checks.length}>Не менее 12 символов</li>
        <li data-met={checks.classes >= 3}>Не менее трёх типов знаков</li>
      </ul>
    </div>
  )
}
```

`RolePicker` keeps native radios and these exact role descriptions:

```ts
export const REGISTER_ROLE_COPY = {
  operator: 'Контроль парка, задачи и репорты',
  mechanic: 'Работы на площадке и проверка роботов',
  driver: 'Поиск и Проверка робота перед продолжением работы',
} as const
```

In `AuthLayout.css`, use only semantic variables and these layout rules:

```css
.auth-layout { min-block-size: 100dvh; display: grid; place-items: center; padding: var(--rp-space-4); background: var(--rp-canvas); }
.auth-card { inline-size: min(100%, 32rem); display: grid; gap: var(--rp-space-4); padding: var(--rp-space-6); border: 1px solid var(--rp-border); border-radius: var(--rp-radius-panel); background: var(--rp-surface-elevated); }
.auth-mark { inline-size: 3rem; block-size: 3rem; display: grid; place-items: center; color: var(--rp-action-on); background: var(--rp-action); border-radius: var(--rp-radius-control); }
.auth-footer { color: var(--rp-text-muted); }
@media (max-width: 599px) { .auth-layout { place-items: start center; padding: var(--rp-space-3); } .auth-card { padding: var(--rp-space-4); border-radius: var(--rp-radius-control); } .auth-card input, .auth-card select { font-size: 1rem; } }
```

- [ ] **Step 4: Implement the three pages with explicit state transitions**

For login, preserve the current username convenience but never store a password. Extract the async request into `submitCredentials()` so both native form submit and the classified retry action use one path. Its error branch is context-aware:

```tsx
const submitCredentials = async () => {
  setError(null)
  setSubmitting(true)
  try {
    const authenticated = await login(username.trim(), password, rememberMe)
    window.localStorage.setItem(LAST_USERNAME_KEY, username.trim())
    setPassword('')
    navigate(landingPathForUser(authenticated), { replace: true })
  } catch (caught) {
    const failure = caught instanceof ApiError && caught.status === 401
      ? {
          kind: 'unauthorized', title: 'Не удалось войти',
          description: mapLoginError(caught), retryable: false,
          ...(caught.requestId ? { requestId: caught.requestId } : {}),
        }
      : classifyApiError(caught, 'Не удалось войти.')
    setError(failure)
    if (!failure.retryable) setPassword('')
  } finally {
    setSubmitting(false)
  }
}

const submit = (event: FormEvent<HTMLFormElement>) => {
  event.preventDefault()
  void submitCredentials()
}
```

Render that structured value through `ErrorState`; pass `onRetry={error.retryable ? submitCredentials : undefined}`. `mapLoginError` is allowed only for a real login `401`; update its tests so non-401 errors are not presented as invalid credentials.

Catch `localStorage` failures locally as the current page does. Registration calls only:

```tsx
await api.register(sharedPassword, username.trim(), password, role)
setSharedPassword('')
setPassword('')
navigate('/login', { replace: true, state: { registrationSuccess: true } })
```

On registration failure, classify with `classifyApiError(error, ru.auth.registrationFailed)` and render its title/description through `ErrorState`; clear `sharedPassword` and `password` for non-retryable failure, but retain them only in mounted state for an explicit retryable transport retry. Keep username/role in either case. Forced password change must verify match and `passwordChecks(newPassword).ok`, call `api.changePassword`, call `refreshUser`, then navigate with `landingPathForUser(refreshed)`. Its request helper maps only `ApiError(401)` to `{ title: 'Текущий пароль не принят', retryable: false }`; known password-policy details use safe `mapApiError`, and all timeout/offline/5xx failures use `classifyApiError` with request ID and retry. Clear all three password fields after success or any non-retryable failure; retain them only for an explicit retryable transport attempt and clear them once that attempt succeeds. Never persist them, reuse `mapLoginError` for password change, or label a non-401 transport failure as bad credentials.

All three pages use `Button busy={submitting}`, native form submit, `autoComplete`, and an inline success/error announcement. Pass `AUTH_HEADING.login`, `.register`, and `.changePassword` respectively to `AuthLayout`; no brand-only or route-label-only alternate `h1` is allowed. Login and register redirect an already authenticated user through `landingPathForUser`.

- [ ] **Step 5: Register pages and approved copy**

In `routeManifest.ts`, ensure these exact entries exist without `nav`:

```ts
{ id: 'login', path: '/login', label: 'Вход', icon: 'robot', surface: 'public' },
{ id: 'register', path: '/register', label: 'Регистрация', icon: 'users', surface: 'public' },
{ id: 'change-password', path: '/change-password', label: 'Смена пароля', icon: 'safety', surface: 'standalone' },
```

Map them in `ROUTE_ELEMENTS`:

```tsx
login: <LoginPage />,
register: <RegisterPage />,
'change-password': <ChangePasswordPage />,
```

Update `ru.auth` with labels used by these pages and map `too_many_attempts`, password-policy details and maintenance through `mapApiError`. Keep headings sourced from `AUTH_HEADING`, not duplicated in `ru.ts`. Remove all user-facing `Emergency` from `RolePicker` copy.

- [ ] **Step 6: Replace old page imports and remove migrated pages**

After `AppRouter.tsx` imports only the new domain pages, delete the three old files listed above. Do not delete `components/ui/PasswordField.tsx` or `RolePicker.tsx` yet because the legacy admin screen still imports them until Tasks 6–12.

- [ ] **Step 7: Run GREEN and frontend gates**

Run:

```bash
cd apps/web
npm test -- \
  src/domains/onboarding/onboarding.test.tsx \
  src/lib/passwordChecks.test.ts \
  src/i18n/errors.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
```

Expected: focused tests, lint, build and nav parity pass; a repository search in live UI files returns no auth-role `Emergency` copy:

```bash
rg -n "Emergency" src/domains/onboarding src/app/routing src/i18n/ru.ts
```

Expected: no matches in onboarding copy; technical i18n keys outside this domain may remain until their owning phase.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/domains/onboarding \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/AppRouter.tsx \
  apps/web/src/i18n/ru.ts \
  apps/web/src/i18n/errors.ts \
  apps/web/src/i18n/errors.test.ts \
  apps/web/src/pages/Login.tsx \
  apps/web/src/pages/Register.tsx \
  apps/web/src/pages/ChangePassword.tsx
git commit -m "feat(web): rebuild authentication experience"
```

---

### Task 4: Build access-state recovery and account journeys

**Files:**

- Create: `apps/web/src/domains/onboarding/components/AccessStatePage.tsx`
- Create: `apps/web/src/domains/onboarding/components/AccessStatePage.css`
- Create: `apps/web/src/domains/onboarding/hooks/useAccessRefresh.ts`
- Create: `apps/web/src/domains/onboarding/pages/AccessPendingPage.tsx`
- Create: `apps/web/src/domains/onboarding/pages/AccessRejectedPage.tsx`
- Create: `apps/web/src/domains/onboarding/pages/MechanicNoParkPage.tsx`
- Create: `apps/web/src/domains/onboarding/pages/NoCabinetPage.tsx`
- Create: `apps/web/src/domains/onboarding/pages/AccountPage.tsx`
- Create: `apps/web/src/domains/onboarding/components/ParkAccessPanel.tsx`
- Create: `apps/web/src/domains/onboarding/components/ParkAccessPanel.test.tsx`
- Create: `apps/web/src/domains/onboarding/accessStates.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/i18n/ru.ts`
- Delete: `apps/web/src/pages/OperatorPending.tsx`
- Delete: `apps/web/src/pages/OperatorRejected.tsx`
- Delete: `apps/web/src/pages/MechanicNoPark.tsx`
- Delete: `apps/web/src/pages/NoCabinet.tsx`

**Interfaces:**

- Consumes: `refreshUser() -> Promise<User>`, `landingPathForUser(user) -> string`, `useTheme()`, `useParkScope().refreshParks()`, the existing operator park-request API, and Foundation status/feedback/overlay primitives.
- Produces: `useAccessRefresh(): { checkedAt: Date | null; initializing: boolean; pending: boolean; error: DomainError | null; refresh(): Promise<void> }`; five route pages.
- Preserves the useful `/operator/parks` capability inside `/account?section=parks`; the old route becomes a non-navigation compatibility redirect rather than a second screen.
- No automatic polling. “Обновлено” means the time this browser successfully checked `/auth/me`, not a fabricated server approval timestamp.

- [ ] **Step 1: Write recovery-path tests**

Add the following to `accessStates.test.tsx` with a local `AuthContext` fixture and `MemoryRouter` routes:

```tsx
it('refreshes pending status manually and leaves the standalone flow when approved', async () => {
  const refreshUser = vi.fn()
    .mockResolvedValueOnce(pendingUser)
    .mockResolvedValueOnce({ ...pendingUser, access_status: 'approved' })
  renderAccess(<AccessPendingPage />, pendingUser, { refreshUser })
  expect(await screen.findByText(/Обновлено в \d{2}:\d{2}/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Проверить статус' }))
  await waitFor(() => expect(refreshUser).toHaveBeenCalledTimes(2))
  expect(screen.getByTestId('current-location')).toHaveTextContent('/overview')
})

it('keeps a rejected user on the page and explains the real next step', () => {
  renderAccess(<AccessRejectedPage />, rejectedUser)
  expect(screen.getByText(/автоматический пересмотр не запланирован/i)).toBeInTheDocument()
  expect(screen.getByText(/владельцем платформы/i)).toBeInTheDocument()
})

it('lets a mechanic recheck park assignment without signing out', async () => {
  const refreshUser = vi.fn().mockResolvedValue({ ...mechanicUser, parks: [park] })
  renderAccess(<MechanicNoParkPage />, mechanicUser, { refreshUser })
  fireEvent.click(screen.getByRole('button', { name: 'Проверить назначение' }))
  await waitFor(() => expect(screen.getByTestId('current-location')).toHaveTextContent('/overview'))
})

it('switches account theme without losing the account route', () => {
  renderAccount(<AccountPage />, approvedUser)
  fireEvent.click(screen.getByRole('radio', { name: 'Тёмная' }))
  expect(setPreference).toHaveBeenCalledWith('dark')
  expect(screen.getByTestId('current-location')).toHaveTextContent('/account')
})

it('persists the desktop density preference through the Foundation provider', () => {
  window.localStorage.removeItem('robopark-density')
  installMatchMedia({ narrow: false, dark: false })
  renderAccountWithTheme(<AccountPage />, approvedUser)

  fireEvent.click(screen.getByRole('radio', { name: 'Компактная' }))

  expect(window.localStorage.getItem('robopark-density')).toBe('compact')
  expect(document.documentElement.dataset.density).toBe('compact')
  expect(screen.getByTestId('current-location')).toHaveTextContent('/account')
})

it('explains why a saved compact preference resolves to comfortable on a phone', () => {
  window.localStorage.setItem('robopark-density', 'compact')
  installMatchMedia({ narrow: true, dark: false })
  renderAccountWithTheme(<AccountPage />, approvedUser)

  expect(screen.getByRole('radio', { name: 'Компактная' })).toBeChecked()
  expect(document.documentElement.dataset.density).toBe('comfortable')
  expect(screen.getByText('На телефоне используется комфортная плотность')).toBeInTheDocument()
})
```

Define `installMatchMedia({ narrow, dark })` in the same test file as a standards-shaped `window.matchMedia` stub whose `matches` value is selected by the exact Foundation queries `(max-width: 899px)` and `(prefers-color-scheme: dark)` and which implements `addEventListener`/`removeEventListener`. `renderAccountWithTheme` wraps the existing router/auth fixture in the real Foundation `ThemeProvider`; do not mock `useTheme` in these two persistence tests.

Freeze time and assert the first successful automatic check renders its real local `HH:mm`. Add a failed-initial-check case: it shows `Проверяем статус…` while pending, then a classified error and retry with no fabricated “Обновлено”; after a successful retry it displays the new check time. Assert no timer or second request occurs without the explicit button—this is one initial load, not polling.

Create `ParkAccessPanel.test.tsx` with an approved operator, two synthetic available parks and one pending request. Assert that opening `Запросить доступ к парку` uses Foundation `Dialog`, submitting park `9` calls `requestPark(9)`, keeps the dialog open on a classified `409`, and on success closes it, refreshes the request list, and calls `refreshParks()`. A second test renders a mechanic and asserts that no operator request controls or `/api/operator/*` calls exist.

- [ ] **Step 2: Run RED**

```bash
cd apps/web
npm test -- src/domains/onboarding/accessStates.test.tsx
```

Expected: FAIL because pages/hook are absent.

- [ ] **Step 3: Implement one manual refresh controller**

`useAccessRefresh.ts` must be complete and deterministic under fake timers:

```tsx
export function useAccessRefresh() {
  const { refreshUser } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)
  const [pending, setPending] = useState(false)
  const [initializing, setInitializing] = useState(true)
  const [error, setError] = useState<DomainError | null>(null)

  const refresh = useCallback(async () => {
    setPending(true)
    setError(null)
    try {
      const refreshed = await refreshUser()
      setCheckedAt(new Date())
      const next = landingPathForUser(refreshed)
      if (next !== location.pathname) navigate(next, { replace: true })
    } catch (caught) {
      setError(classifyApiError(caught, 'Не удалось проверить статус доступа.'))
    } finally {
      setPending(false)
      setInitializing(false)
    }
  }, [location.pathname, navigate, refreshUser])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return { checkedAt, initializing, pending, error, refresh }
}
```

The effect performs exactly one check for the mounted standalone route; it schedules no timer. `refresh` stays referentially stable while the path does not change. During `initializing`, render `LoadingState` text `Проверяем статус…`; after completion, render “Обновлено в HH:mm” only when `checkedAt` is non-null.

- [ ] **Step 4: Implement truthful standalone status pages**

`AccessStatePage` public interface:

```ts
export type AccessStatePageProps = {
  tone: 'info' | 'warning' | 'critical'
  title: string
  description: string
  nextStep: string
  refreshLabel: string
  checkedAt: Date | null
  pending: boolean
  initializing: boolean
  error: DomainError | null
  onRefresh(): void
  onLogout(): void
}
```

Render, in order: `StatusBadge`, title, description, “Следующий шаг”, a visible support block reading `Контакт: владелец платформы через рабочий канал команды`, initial-check state or successful check time formatted as `HH:mm`, `ErrorState`, refresh button, logout button. Pending copy says approval and optional park assignment are expected. Rejected copy explicitly says automatic review is not promised. No-park copy says the mechanic can recheck assignment immediately and does not need to log out. No-cabinet copy names the current role and asks an administrator to assign a supported role/capability.

Each page uses `useAccessRefresh`; only button text differs (`Проверить статус` versus `Проверить назначение`).

- [ ] **Step 5: Implement `/account` as settings plus operator park access**

`AccountPage` displays immutable username/role/access status, assigned parks, and the Foundation theme/density preferences. Destructure `preference`, `setPreference`, `densityPreference`, `resolvedDensity`, and `setDensityPreference` from the one `useTheme()` call; do not read or write either storage key in the page. Render this exact theme selector:

```tsx
const themeOptions: { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'Системная' },
  { value: 'light', label: 'Светлая' },
  { value: 'dark', label: 'Тёмная' },
]

<fieldset className="account-theme">
  <legend>Тема оформления</legend>
  {themeOptions.map((option) => (
    <label key={option.value}>
      <input
        checked={preference === option.value}
        name="theme-preference"
        onChange={() => setPreference(option.value)}
        type="radio"
      />
      {option.label}
    </label>
  ))}
</fieldset>
```

Immediately after it render this exact density selector:

```tsx
const densityOptions: { value: DensityPreference; label: string }[] = [
  { value: 'comfortable', label: 'Комфортная' },
  { value: 'compact', label: 'Компактная' },
]

<fieldset className="account-density">
  <legend>Плотность интерфейса</legend>
  {densityOptions.map((option) => (
    <label key={option.value}>
      <input
        checked={densityPreference === option.value}
        name="density-preference"
        onChange={() => setDensityPreference(option.value)}
        type="radio"
      />
      {option.label}
    </label>
  ))}
  {resolvedDensity !== densityPreference ? (
    <p>На телефоне используется комфортная плотность</p>
  ) : null}
</fieldset>
```

The radio reflects the persisted desktop preference even when `resolvedDensity` is forced to `comfortable` at widths `<= 899px`; returning to desktop is handled by `ThemeProvider` and restores compact without another click. Both fieldsets keep 44 px label targets.

Logout uses `await logout()` then `navigate('/login', { replace: true })`. Do not expose numeric permission lists on the account page.

Below assigned parks, render `ParkAccessPanel` only when `user.role === 'operator' && user.access_status === 'approved'`. Use this exact injectable transport boundary:

```ts
export type ParkAccessApi = Pick<
  typeof api,
  'availableParks' | 'operatorParkRequests' | 'requestPark'
>

export type ParkAccessPanelProps = {
  apiClient?: ParkAccessApi
  refreshParks(): Promise<void>
  user: User
}
```

`ParkAccessPanel` loads `availableParks()` and `operatorParkRequests()` through separate resources, keeps their failures local, and renders assigned parks from `user.parks`. Pending/rejected/approved requests always have text badges. `Запросить доступ к парку` opens a Foundation `Dialog` with one labelled select and explicit cancel/submit actions. Successful `requestPark(parkId)` refreshes available parks, request history, and Foundation `refreshParks()` before closing; a classified failure remains inside the open dialog with `role="alert"`. No timer auto-closes success, and the component never fabricates a park name when an old request exposes only `park_id`.

`AccountPage` reads `section`; when it equals `parks`, focus the park-access heading after mount without changing history. Other unknown values are removed with `replace: true`. This gives the compatibility redirect a stable target while keeping `/account` canonical.

- [ ] **Step 6: Register status/account routes and landing behavior**

Keep the four existing Foundation status IDs, update their page metadata as needed, and add only `account` to `AppRouteId`/`ROUTE_MANIFEST`:

```ts
{ id: 'access-pending', path: '/access/pending', legacyPaths: ['/operator/pending'], label: 'Ожидание доступа', icon: 'clock', surface: 'standalone' },
{ id: 'access-rejected', path: '/access/rejected', legacyPaths: ['/operator/rejected'], label: 'Доступ отклонён', icon: 'critical', surface: 'standalone' },
{ id: 'mechanic-no-park', path: '/mechanic/no-park', label: 'Парк не назначен', icon: 'parks', surface: 'standalone' },
{ id: 'no-cabinet', path: '/no-cabinet', label: 'Кабинет недоступен', icon: 'warning', surface: 'standalone' },
{ id: 'account', path: '/account', label: 'Аккаунт', icon: 'settings', prerequisites: ['password-changed', 'approved'], surface: 'shell', nav: { group: 'collaboration', desktopOrder: 990 } },
```

Map all five page elements in `ROUTE_ELEMENTS`. Update `landingPathForUser` so `must_change_password` wins, then pending/rejected, then mechanic-without-park, then the role’s first accessible shell route, then `/no-cabinet`. Extend the existing access-policy table tests for this exact precedence.

Keep the existing Foundation `operator-parks` ID as a non-nav compatibility item at `/operator/parks`, remove any `nav` metadata from it, and replace its registry element with `<Navigate replace to="/account?section=parks" />`. Add a direct-route test proving the redirect preserves no legacy or secret query keys and does not add browser history. Do not append a duplicate manifest record.

- [ ] **Step 7: Remove old standalone pages and run GREEN**

Delete the four old status page files only after `AppRouter` has no imports from them. Keep the now-dead `pages/OperatorParks.tsx` and `components/parks/RequestParkModal.tsx` temporarily because other dead legacy pages still import the modal and TypeScript compiles the whole source tree; Plan 05 deletes that dependency cluster atomically. Run:

```bash
cd apps/web
npm test -- \
  src/domains/onboarding/accessStates.test.tsx \
  src/domains/onboarding/components/ParkAccessPanel.test.tsx \
  src/app/routing/accessPolicy.test.ts \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
```

Expected: all tests and gates pass; pending/rejected/no-park precedence is table-tested.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/domains/onboarding \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/accessPolicy.ts \
  apps/web/src/app/routing/AppRouter.tsx \
  apps/web/src/i18n/ru.ts \
  apps/web/src/pages/OperatorPending.tsx \
  apps/web/src/pages/OperatorRejected.tsx \
  apps/web/src/pages/MechanicNoPark.tsx \
  apps/web/src/pages/NoCabinet.tsx
git commit -m "feat(web): add recoverable access and account journeys"
```

---

### Task 5: Deliver the canonical parks module

**Files:**

- Create: `apps/web/src/domains/administration/shared/AdministrationPage.tsx`
- Create: `apps/web/src/domains/administration/shared/AdministrationPage.css`
- Create: `apps/web/src/domains/administration/shared/adminConflict.ts`
- Create: `apps/web/src/domains/administration/shared/adminConflict.test.ts`
- Create: `apps/web/src/domains/administration/parks/parkDraft.ts`
- Create: `apps/web/src/domains/administration/parks/parkDraft.test.ts`
- Create: `apps/web/src/domains/administration/parks/parkReadiness.ts`
- Create: `apps/web/src/domains/administration/parks/parkReadiness.test.ts`
- Create: `apps/web/src/domains/administration/parks/ParkRequestQueue.tsx`
- Create: `apps/web/src/domains/administration/parks/ParkEditor.tsx`
- Create: `apps/web/src/domains/administration/parks/ParksAdminPage.tsx`
- Create: `apps/web/src/domains/administration/parks/ParksAdminPage.css`
- Create: `apps/web/src/domains/administration/parks/ParksAdminPage.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`

**Interfaces:**

- Consumes: `api.adminParks`, `api.adminParkRequests`, `api.createPark`, `api.updatePark`, `api.resolveParkRequest`; Foundation `useParkScope`, `canAccessRoute`, `PageLayout`, `Panel`, `FormField`, `Button`, `StatusBadge`, `ConfirmDialog`, async states.
- Produces: `AdministrationPage`; `isStaleAdminConflict`; `ParkDraft`, `parkToDraft(park)`, `parkDraftToPatch(draft)`; `ParkReadiness`, `parkReadiness(park)`; route ID `admin-parks` at `/admin/parks` gated by `parks.manage`.

- [ ] **Step 1: Test normalization, request decisions and destructive confirmation**

In `parkDraft.test.ts`:

```ts
it('normalizes optional numeric and text fields without inventing values', () => {
  expect(parkDraftToPatch({
    name: ' Север ', tag: ' north ', tracker_queue: ' OPS ', tracker_priority: '',
    tracker_type: '', group_id: '', chat_id: '42', is_active: true,
    feature_reports: true, feature_blockers: false,
    feature_sla_repair: true, feature_backlog_alerts: false,
  })).toEqual({
    name: 'Север', tag: 'north', tracker_queue: 'OPS', tracker_priority: null,
    tracker_type: null, group_id: null, chat_id: 42, is_active: true,
    feature_reports: true, feature_blockers: false,
    feature_sla_repair: true, feature_backlog_alerts: false,
  })
})
```

In `parkReadiness.test.ts`, pin only readiness facts supported by current server fields:

```ts
it('identifies the exact configuration that blocks active park workflows', () => {
  expect(parkReadiness({ ...activePark, tracker_queue: '  ' })).toEqual({
    state: 'attention',
    label: 'Требует настройки',
    issues: [{
      code: 'tracker-queue-missing',
      title: 'Не указана очередь Tracker',
      description: 'Без очереди недоступны обзор, задачи, поиск роботов и Tracker-репорты.',
      field: 'tracker_queue',
    }],
  })
})

it('does not present configuration readiness as live integration health', () => {
  expect(parkReadiness({ ...activePark, tracker_queue: 'ROBOPARK' })).toEqual({
    state: 'ready', label: 'Конфигурация заполнена', issues: [],
  })
})
```

In `ParksAdminPage.test.tsx`, mock all five API methods and assert:

```tsx
it('does not deactivate a park until the shared confirmation succeeds', async () => {
  renderParksPage([activePark])
  fireEvent.click(await screen.findByRole('button', { name: 'Деактивировать Север' }))
  expect(api.updatePark).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Введите ДЕАКТИВИРОВАТЬ north для подтверждения'), {
    target: { value: 'ДЕАКТИВИРОВАТЬ north' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Деактивировать' }))
  await waitFor(() => expect(api.updatePark).toHaveBeenCalledWith(
    7, { is_active: false }, activePark.revision,
  ))
})

it('reloads requests after the server approves one', async () => {
  renderParksPage([activePark], [pendingRequest])
  fireEvent.click(await screen.findByRole('button', { name: 'Одобрить заявку пользователя op' }))
  fireEvent.change(screen.getByLabelText('Введите ОДОБРИТЬ 12 для подтверждения'), {
    target: { value: 'ОДОБРИТЬ 12' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Одобрить доступ' }))
  await waitFor(() => expect(api.resolveParkRequest).toHaveBeenCalledWith(
    12, 'approve', pendingRequest.revision,
  ))
  expect(api.adminParkRequests).toHaveBeenCalledTimes(2)
})

it('keeps a stale draft and requires an explicit decision after reload', async () => {
  vi.spyOn(api, 'updatePark')
    .mockRejectedValueOnce(new ApiError(409, 'stale_revision'))
    .mockResolvedValueOnce({ ...activePark, name: 'Мой вариант', revision: '3' })
  vi.spyOn(api, 'adminParks')
    .mockResolvedValueOnce([activePark])
    .mockResolvedValueOnce([{ ...activePark, name: 'Серверный вариант', revision: '2' }])
  renderParksPage([activePark])
  fireEvent.change(await screen.findByLabelText('Название'), { target: { value: 'Мой вариант' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить парк' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Данные изменены другим пользователем')
  expect(screen.getByLabelText('Название')).toHaveValue('Мой вариант')
  fireEvent.click(screen.getByRole('button', { name: 'Загрузить актуальные данные' }))
  await screen.findByText('Серверный вариант')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить мой черновик' }))
  expect(api.updatePark).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить парк' }))
  await waitFor(() => expect(api.updatePark).toHaveBeenLastCalledWith(
    7, expect.objectContaining({ name: 'Мой вариант' }), '2',
  ))
})
```

- [ ] **Step 2: Run RED**

```bash
cd apps/web
npm test -- \
  src/domains/administration/parks/parkDraft.test.ts \
  src/domains/administration/parks/parkReadiness.test.ts \
  src/domains/administration/shared/adminConflict.test.ts \
  src/domains/administration/parks/ParksAdminPage.test.tsx
```

Expected: FAIL because the parks domain does not exist.

- [ ] **Step 3: Implement a strict park draft boundary**

Use this complete public type in `parkDraft.ts`:

```ts
export type ParkDraft = {
  name: string
  tag: string
  tracker_queue: string
  tracker_priority: string
  tracker_type: string
  group_id: string
  chat_id: string
  is_active: boolean
  feature_reports: boolean
  feature_blockers: boolean
  feature_sla_repair: boolean
  feature_backlog_alerts: boolean
}

function optionalInteger(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  if (!Number.isSafeInteger(parsed)) throw new Error('invalid_integer')
  return parsed
}

export function parkDraftToPatch(draft: ParkDraft): Partial<Park> {
  return {
    name: draft.name.trim(),
    tag: draft.tag.trim(),
    tracker_queue: draft.tracker_queue.trim() || null,
    tracker_priority: draft.tracker_priority.trim() || null,
    tracker_type: draft.tracker_type.trim() || null,
    group_id: optionalInteger(draft.group_id),
    chat_id: optionalInteger(draft.chat_id),
    is_active: draft.is_active,
    feature_reports: draft.feature_reports,
    feature_blockers: draft.feature_blockers,
    feature_sla_repair: draft.feature_sla_repair,
    feature_backlog_alerts: draft.feature_backlog_alerts,
  }
}
```

`parkToDraft` performs the inverse with `String(value ?? '')` and `?? true` defaults matching `Park`.

Create the shared conflict discriminator without replacing Phase 2's general classifier:

```ts
import { ApiError } from '../../../api'

export const STALE_ADMIN_CONFLICT = {
  title: 'Данные изменены другим пользователем',
  description: 'Загрузите актуальные данные и повторно примите решение.',
} as const

export function isStaleAdminConflict(error: unknown): error is ApiError {
  return error instanceof ApiError
    && error.status === 409
    && error.detail === 'stale_revision'
}
```

`adminConflict.test.ts` distinguishes this exact detail from duplicate-tag, role-in-use and job-in-progress `409` responses; non-stale errors still go through `classifyApiError`.

Create `parkReadiness.ts`:

```ts
import type { Park } from '../../../api'

export type ParkReadinessIssue = {
  code: 'park-inactive' | 'tracker-queue-missing'
  title: string
  description: string
  field: 'is_active' | 'tracker_queue'
}

export type ParkReadiness = {
  state: 'ready' | 'attention' | 'inactive'
  label: string
  issues: ParkReadinessIssue[]
}

export function parkReadiness(park: Park): ParkReadiness {
  if (park.is_active === false) {
    return {
      state: 'inactive',
      label: 'Парк выключен',
      issues: [{
        code: 'park-inactive',
        title: 'Парк исключён из рабочих выборок',
        description: 'Активируйте парк после проверки его конфигурации.',
        field: 'is_active',
      }],
    }
  }
  if (!park.tracker_queue?.trim()) {
    return {
      state: 'attention',
      label: 'Требует настройки',
      issues: [{
        code: 'tracker-queue-missing',
        title: 'Не указана очередь Tracker',
        description: 'Без очереди недоступны обзор, задачи, поиск роботов и Tracker-репорты.',
        field: 'tracker_queue',
      }],
    }
  }
  return { state: 'ready', label: 'Конфигурация заполнена', issues: [] }
}
```

Do not derive failures from unused `group_id`/`chat_id`, optional Tracker defaults, photos, cached metrics, or client reachability. This helper reports configuration completeness only, never live integration health.

- [ ] **Step 4: Build cards, editor and request queue**

`AdministrationPage` wraps `PageLayout`, displays the current module title/subtitle, and renders a compact “Управление” breadcrumb; module availability continues to come from RouteManifest/AppShell, not a hard-coded second nav.

`ParkEditor` public contract:

```ts
export type ParkEditorProps = {
  park: AdminPark
  canCheckIntegration: boolean
  pending: boolean
  onSave(parkId: number, patch: Partial<Park>, revision: AdminRevision): Promise<void>
  onToggleActive(park: AdminPark): void
}
```

Render semantic groups “Основное”, “Tracker”, “Каналы” and “Возможности”. Above them render “Готовность конфигурации” with textual `StatusBadge`, each exact issue reason, and an action (`Активировать парк` or `Указать очередь`) that focuses the referenced form control. Ready copy must include “Проверка заполненности, а не доступности интеграции” and show a “Проверить Tracker” link only when `canCheckIntegration` is true. `ParksAdminPage` computes that prop once with `canAccessRoute(actor, 'admin-integrations')`; the editor does not duplicate access policy. Validate name/tag and integer inputs locally; show exact field error next to the invalid control. Saving awaits `api.updatePark(park.id, patch, park.revision)`, then replaces the row with the returned server object.

`ParkRequestQueue` receives `requests`, `parks`, `pending`, `onResolve`. Approve and reject open `ConfirmDialog` with the canonical `ОДОБРИТЬ ${request.id}` / `ОТКЛОНИТЬ ${request.id}` phrase; the description names user and park. On success the parent refetches `api.adminParkRequests()` and closes the dialog. On failure it keeps the dialog open with the classified error.

`ParksAdminPage` loads parks and requests independently, so one failed resource does not erase the other. Creation uses a small `Dialog`, posts only `{ name, tag }`, closes on server success, and selects the returned park.

The page owns its complete administration list, but Foundation owns the global selected park. Read `parkId`, `setParkId`, and `refreshParks` from `useParkScope()`; never parse, validate, store, or rewrite `PARK_QUERY_KEY` inside this domain. Select the local editor row whose ID equals `parkId`, and show `EmptyState` if no server rows exist. For deactivation pass `confirmationPhrase={'ДЕАКТИВИРОВАТЬ ' + park.tag}`. After a successful park create/update/deactivation, update or refetch the module list from the returned server state, `await refreshParks()`, then select the created/remaining valid ID through `setParkId`. `ParkScopeProvider` alone performs invalid-ID fallback and URL/session-storage synchronization.

On `stale_revision`, preserve the exact `ParkDraft`, disable another save until the user chooses, and show `STALE_ADMIN_CONFLICT` with `Загрузить актуальные данные`. That action calls `api.adminParks()`, stores the latest matching row beside the draft, and lists changed field labels without showing secret values. `Принять актуальную версию` replaces the draft; `Сохранить мой черновик` keeps it but advances the base revision. Neither choice sends a mutation. A later explicit save sends the new revision; a destructive dialog also clears its phrase input and requires the canonical phrase again.

- [ ] **Step 5: Add responsive module styles**

In `ParksAdminPage.css`:

```css
.parks-admin-grid { display: grid; grid-template-columns: minmax(16rem, 22rem) minmax(0, 1fr); gap: var(--rp-space-4); }
.park-card-list { display: grid; gap: var(--rp-space-2); list-style: none; padding: 0; margin: 0; }
.park-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--rp-space-4); }
@media (max-width: 899px) { .parks-admin-grid, .park-form-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 599px) { .parks-admin-grid { gap: var(--rp-space-3); } .park-form-grid input, .park-form-grid select { font-size: 1rem; } }
```

Every card button has a visible text name and minimum block size 44 px. Do not render a horizontally scrolling table.

- [ ] **Step 6: Register the canonical route**

Add to the manifest and element registry:

```ts
{
  id: 'admin-parks', path: '/admin/parks', label: 'Парки', icon: 'parks',
  permission: 'parks.manage', prerequisites: ['password-changed', 'approved'],
  surface: 'shell', nav: { group: 'administration', desktopOrder: 10 },
},
```

```tsx
'admin-parks': <ParksAdminPage />,
```

- [ ] **Step 7: Run GREEN and commit**

```bash
cd apps/web
npm test -- \
  src/domains/administration/parks/parkDraft.test.ts \
  src/domains/administration/parks/parkReadiness.test.ts \
  src/domains/administration/shared/adminConflict.test.ts \
  src/domains/administration/parks/ParksAdminPage.test.tsx \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
git add src/domains/administration/parks src/domains/administration/shared \
  src/app/routing/routeManifest.ts src/app/routing/AppRouter.tsx
git commit -m "feat(web): add canonical parks administration"
```

Expected: all gates pass and the route appears only for `parks.manage` users.

---

### Task 6: Deliver access, users and registration-gate administration

**Files:**

- Modify: `apps/api/src/robopark_api/routers/admin_users.py`
- Modify: `apps/api/src/robopark_api/routers/admin_roles.py`
- Modify: `apps/api/tests/test_admin_user_access.py`
- Modify: `apps/api/tests/test_admin_roles.py`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/domains/administration/access/userDraft.ts`
- Create: `apps/web/src/domains/administration/access/userDraft.test.ts`
- Create: `apps/web/src/domains/administration/access/AccessQueue.tsx`
- Create: `apps/web/src/domains/administration/access/UserDirectory.tsx`
- Create: `apps/web/src/domains/administration/access/UserEditor.tsx`
- Create: `apps/web/src/domains/administration/access/RegistrationGateCard.tsx`
- Create: `apps/web/src/domains/administration/access/AccessAdminPage.tsx`
- Create: `apps/web/src/domains/administration/access/AccessAdminPage.css`
- Create: `apps/web/src/domains/administration/access/AccessAdminPage.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`

**Interfaces:**

- Consumes: users/roles/permissions/parks APIs and registration-password APIs already defined in `api.ts`; `ConfirmDialog` phrase support.
- Produces: additive backend/frontend `PermissionOut.is_privileged` / `PermissionCatalogItem.is_privileged`; `UserDraft`, `userToDraft`, `buildUserUpdate`, and a helper that compares desired privileged grants with the user's pre-mutation effective/direct grants; route ID `admin-access` at `/admin/access` gated by `users.manage`.
- Security: admin may manage users but only royal may approve/reject registrations, modify access status, create another royal, grant a new privileged permission to a user, or configure the shared registration password. When a combined create/update adds one or more privileged grants, the royal UI requires exactly one `ВЫДАТЬ ПРИВИЛЕГИИ ${user.username}` confirmation for that save; non-royal UI does not offer those grants, and the server remains authoritative.

- [ ] **Step 1: Test safe payload construction and critical actions**

`userDraft.test.ts`:

```ts
it('omits an empty replacement password and serializes permission overrides', () => {
  expect(buildUserUpdate({
    role_slug: 'mechanic', access_status: 'approved', is_active: true,
    must_change_password: true, tracker_login: ' mech ', password: '',
    park_ids: [7], permissions: new Set(['nav.tasks', 'tracker.read']),
  }, true)).toEqual({
    role_slug: 'mechanic', access_status: 'approved', is_active: true,
    must_change_password: true, tracker_login: 'mech', park_ids: [7],
    permissions: ['nav.tasks', 'tracker.read'],
  })
})

it('never includes access_status for a non-royal actor', () => {
  const approvedDraft: UserDraft = {
    role_slug: 'operator', access_status: 'approved', is_active: true,
    must_change_password: false, tracker_login: '', password: '', park_ids: [7],
    permissions: new Set(['nav.dashboard']),
  }
  expect(buildUserUpdate(approvedDraft, false)).not.toHaveProperty('access_status')
})
```

`AccessAdminPage.test.tsx`:

```tsx
it('requires the username phrase before deleting an account', async () => {
  renderAccessAdmin({ actor: royal, users: [managedUser] })
  fireEvent.click(await screen.findByRole('button', { name: 'Удалить аккаунт mech' }))
  fireEvent.change(screen.getByLabelText('Введите mech для подтверждения'), {
    target: { value: 'wrong' },
  })
  expect(screen.getByRole('button', { name: 'Удалить аккаунт' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Введите mech для подтверждения'), {
    target: { value: 'mech' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Удалить аккаунт' }))
  await waitFor(() => expect(api.deleteAdminUser).toHaveBeenCalledWith(
    managedUser.id, managedUser.revision,
  ))
})

it('requires the exact phrase for each critical account change', async () => {
  renderAccessAdmin({ actor: royal, users: [managedUser] })
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Аккаунт активен' }))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить пользователя' }))
  expect(screen.getByRole('button', { name: 'Деактивировать аккаунт' })).toBeDisabled()
  fireEvent.change(
    screen.getByLabelText('Введите ДЕАКТИВИРОВАТЬ mech для подтверждения'),
    { target: { value: 'ДЕАКТИВИРОВАТЬ mech' } },
  )
  fireEvent.click(screen.getByRole('button', { name: 'Деактивировать аккаунт' }))
  await waitFor(() => expect(api.updateAdminUser).toHaveBeenCalledWith(
    managedUser.id, expect.objectContaining({ is_active: false }), managedUser.revision,
  ))
})

it('never renders the registration password returned by a legacy response', async () => {
  renderAccessAdmin({
    actor: royal,
    registration: { configured: true, password_masked: '•••• (24)', updated_at: null },
  })
  expect(await screen.findByText('Регистрация открыта')).toBeInTheDocument()
  expect(screen.queryByText('•••• (24)')).not.toBeInTheDocument()
})
```

Add RED UI cases for both user creation and update. In each case the catalog marks `system.manage` with `is_privileged: true`; selecting one or several newly privileged permissions must not call `createAdminUser`/`updateAdminUser` until the exact `ВЫДАТЬ ПРИВИЛЕГИИ ${user.username}` phrase is entered (for create, `user.username` is the normalized username draft). The update case starts from explicit `permissions` and `role_permissions`, then combines a privileged grant with deactivation and password replacement: walk the required dialogs, assert the privileged phrase appears exactly once for that combined save, and assert exactly one final PATCH contains all changes. Add a non-royal case asserting privileged catalog toggles/actions are absent even though ordinary permissions remain editable.

Parameterize create and update password tests over success, a classified non-conflict error, and `ApiError(409, 'stale_revision')`. After every settled attempt, assert the password input is empty. On error the non-secret draft remains; on conflict the current server row is loaded, the secret and typed confirmation are gone, no automatic retry occurs, and an explicit new save must repeat every applicable phrase. This is a `finally` invariant for both create and update, not only a success-path cleanup.

In `test_admin_user_access.py`, add HTTP RED coverage proving a non-royal `users.manage` actor receives `403 privileged_grant_forbidden` when either creating a user with a privileged permission, updating direct permissions to add one, or changing the user's role so the final effective set newly gains one. The tests assert no user/role/permission mutation persists. Add royal create/update positive cases. The backend must snapshot the target's effective permissions and positive direct overrides **before** changing role or overrides, compute the desired final effective/direct set, and call `rbac.privileged_grant_blocked` against that before mutation; comparing against the already changed role is forbidden. In `test_admin_roles.py`, assert the permission catalog exposes `is_privileged=True` exactly for `rbac.PRIVILEGED_PERMISSIONS`.

- [ ] **Step 2: Run RED**

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_admin_user_access.py tests/test_admin_roles.py
uv run --frozen --extra dev ruff check src/robopark_api/routers/admin_users.py \
  src/robopark_api/routers/admin_roles.py tests/test_admin_user_access.py tests/test_admin_roles.py
cd ../web
npm test -- \
  src/domains/administration/access/userDraft.test.ts \
  src/domains/administration/access/AccessAdminPage.test.tsx
cd ../..
```

Expected: FAIL because the catalog flag, pre-mutation grant comparison and access domain are absent.

- [ ] **Step 3: Define the draft boundary and URL filters**

Use the exact type:

```ts
export type UserDraft = {
  role_slug: string
  access_status: string
  is_active: boolean
  must_change_password: boolean
  tracker_login: string
  password: string
  park_ids: number[]
  permissions: Set<string>
}
```

`buildUserUpdate(draft, isRoyal)` returns `Parameters<typeof api.updateAdminUser>[1]`, sorts `park_ids` and `permissions` numerically/lexically, serializes an empty trimmed `tracker_login` as `null`, omits an empty `password`, and includes `access_status` only for royal. Add `is_privileged: bool` to backend `PermissionOut` and `is_privileged: boolean` to frontend `PermissionCatalogItem`; serialize it as `item.key in rbac.PRIVILEGED_PERMISSIONS`. The user-grant helper takes the server catalog, the target's original `permissions` and `role_permissions` (positive direct grants are the effective keys absent from role keys), and the desired final permissions. It returns only privileged desired keys absent from the union of the original effective/direct grants. For create, that original union is empty, so a privileged grant inherited through the selected role is still a new grant to this user.

`AccessAdminPage` owns search params `user`, `role`, `status`, `q`. Query writes clone the current `URLSearchParams` and replace only those four keys, so Foundation's `park` scope is preserved. `UserDirectory` derives its list without mutating server rows. Unknown filter values are removed; unknown `user` selects the first pending account, then the first account.

- [ ] **Step 4: Split access queue, directory and editor into explicit operations**

`AccessQueue` lists pending users first. Only a royal actor sees approve/reject controls; a non-royal `users.manage` actor sees the pending state without an action it cannot execute. Approve and reject each open controlled `ConfirmDialog` with `ОДОБРИТЬ ${user.username}` / `ОТКЛОНИТЬ ${user.username}`; approve names selected parks, reject explains there is no automatic re-review. Both send `user.revision`, await the server and refetch users before success is shown.

`UserEditor` receives this contract:

```ts
export type UserEditorProps = {
  actor: User
  user: AdminUser
  roles: AdminRole[]
  catalog: PermissionCatalogItem[]
  parks: Park[]
  pending: boolean
  onSave(
    userId: number,
    payload: Parameters<typeof api.updateAdminUser>[1],
    revision: AdminRevision,
  ): Promise<void>
  onDelete(user: AdminUser): void
}
```

Keep server protections visible: own account has no delete action; non-royal cannot edit a royal or access status; last-royal errors stay in the local editor as `ErrorState`. Saving a draft that deactivates an account, demotes a royal, includes a replacement password, or adds any privileged grant opens controlled `ConfirmDialog` gates before the request with the exact phrase `ДЕАКТИВИРОВАТЬ ${user.username}`, `ПОНИЗИТЬ ${user.username}`, `СБРОСИТЬ ПАРОЛЬ ${user.username}`, or `ВЫДАТЬ ПРИВИЛЕГИИ ${user.username}` respectively. If more than one condition is true, show one dialog at a time in that order and send one combined request only after all required confirmations complete; the privileged gate appears exactly once regardless of how many privileged keys the save adds. A royal sees privileged catalog choices, while a non-royal receives only ordinary grant controls; neither UI branch replaces backend authorization. Account deletion always uses `confirmationPhrase={user.username}` and closes only after `deleteAdminUser(user.id, user.revision)` resolves and the list refetches.

Creation is in a `Dialog`; submit username, password, role, parks, tracker login and permission overrides through `api.createAdminUser`. Run the same privileged-delta check against an empty original grant set and require the username-specific privileged phrase for royal actors when needed. Create and update both clear their password state in `finally`, including server error and stale conflict. Never store draft secrets in URL/resource cache.

- [ ] **Step 5: Move the shared registration gate into access administration**

`RegistrationGateCard` renders only `configured`, `updated_at`, `encrypted`, and opaque `revision`; it never renders `password_masked`. Its form uses local password state and `PasswordRequirements`. PUT opens a controlled confirmation with `confirmationPhrase="ОТКРЫТЬ РЕГИСТРАЦИЮ"`, sends the status `revision`, clears the secret in `finally`, and renders “Регистрация открыта” only from the returned response. DELETE opens a danger `ConfirmDialog` with `confirmationPhrase="ЗАКРЫТЬ"`, calls `clearRegistrationPassword(status.revision)`, and renders “Регистрация закрыта” only from the returned response.

The card is absent for non-royal users. A `403` from any stale UI is classified and displayed without changing local saved state.

- [ ] **Step 6: Assemble independent resource states and responsive layout**

Load `adminUsers`, `adminRoles`, `adminRolePermissionCatalog`, and `parks` independently. Load `registrationPasswordSettings` only when `actor.role === 'royal'`. Render a recoverable `ErrorState` beside the failed resource, not one page-wide generic error. Apply the Task 5 `stale_revision` protocol to user/access and registration mutations: preserve non-secret drafts, clear password values and all typed confirmations after every failed attempt, fetch the latest row/status, and require the user to choose current data or retained non-secret draft before an explicit resubmit. Recompute privileged deltas against the newly loaded effective/direct permissions and require a fresh phrase if the explicit resubmit still grants one. Never carry a password through conflict recovery.

Use:

```css
.access-admin-grid { display: grid; grid-template-columns: minmax(18rem, 24rem) minmax(0, 1fr); gap: var(--rp-space-4); }
.user-editor-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--rp-space-4); }
@media (max-width: 899px) { .access-admin-grid, .user-editor-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 599px) { .user-directory-row { min-block-size: 4.5rem; } .user-editor-actions { position: sticky; inset-block-end: 0; background: var(--rp-surface); } }
```

The mobile user directory is a semantic list of cards, not a table.

- [ ] **Step 7: Register `/admin/access` and run GREEN**

Manifest/registry:

```ts
{
  id: 'admin-access', path: '/admin/access', label: 'Люди и доступ', icon: 'users',
  permission: 'users.manage', prerequisites: ['password-changed', 'approved'],
  surface: 'shell', nav: { group: 'administration', desktopOrder: 20 },
},
```

```tsx
'admin-access': <AccessAdminPage />,
```

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_admin_user_access.py tests/test_admin_roles.py
uv run --frozen --extra dev ruff check src/robopark_api/routers/admin_users.py \
  src/robopark_api/routers/admin_roles.py tests/test_admin_user_access.py tests/test_admin_roles.py
uv run --frozen --extra dev ruff format --check src/robopark_api/routers/admin_users.py \
  src/robopark_api/routers/admin_roles.py tests/test_admin_user_access.py tests/test_admin_roles.py
cd ../web
npm test -- \
  src/domains/administration/access/userDraft.test.ts \
  src/domains/administration/access/AccessAdminPage.test.tsx \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
cd ../..
```

Expected: tests pass; no password/token/cookie appears in a snapshot or URL.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/robopark_api/routers/admin_users.py \
  apps/api/src/robopark_api/routers/admin_roles.py \
  apps/api/tests/test_admin_user_access.py \
  apps/api/tests/test_admin_roles.py \
  apps/web/src/api.ts \
  apps/web/src/domains/administration/access \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/AppRouter.tsx
git commit -m "feat(web): add access and user administration"
```

---

### Task 7: Deliver canonical role administration

**Files:**

- Modify: `apps/api/src/robopark_api/routers/admin_roles.py`
- Modify: `apps/api/tests/test_admin_roles.py`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/domains/administration/roles/roleDraft.ts`
- Create: `apps/web/src/domains/administration/roles/roleDraft.test.ts`
- Create: `apps/web/src/domains/administration/roles/RoleEditor.tsx`
- Create: `apps/web/src/domains/administration/roles/RolesAdminPage.tsx`
- Create: `apps/web/src/domains/administration/roles/RolesAdminPage.css`
- Create: `apps/web/src/domains/administration/roles/RolesAdminPage.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`

**Interfaces:**

- Consumes: `api.adminRoles`, `adminRolePermissionCatalog`, `createAdminRole`, `updateAdminRole`, `deleteAdminRole`.
- Produces: `RoleDraft`, `roleToDraft`, `roleDraftToCreate`, `roleDraftToUpdate`; route `admin-roles` gated by `roles.manage`.

- [ ] **Step 1: Write RED tests for payload and deletion safeguards**

```ts
it('normalizes a new role and sorts capabilities', () => {
  expect(roleDraftToCreate({
    slug: ' field_lead ', name: ' Старший смены ', description: ' Координация ',
    permissions: new Set(['users.manage', 'nav.dashboard']),
  })).toEqual({
    slug: 'field_lead', name: 'Старший смены', description: 'Координация',
    permissions: ['nav.dashboard', 'users.manage'],
  })
})
```

```tsx
it('requires the role slug before deleting a non-system role', async () => {
  renderRolesPage([customRole])
  fireEvent.click(await screen.findByRole('button', { name: 'Удалить роль Старший смены' }))
  expect(api.deleteAdminRole).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Введите field_lead для подтверждения'), {
    target: { value: 'field_lead' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Удалить роль' }))
  await waitFor(() => expect(api.deleteAdminRole).toHaveBeenCalledWith(
    customRole.id, customRole.revision,
  ))
})

it('requires a role-specific phrase before granting a privileged permission', async () => {
  renderRolesPage([customRole], [
    makePermission({ key: 'system.manage', is_privileged: true }),
  ])
  fireEvent.click(await screen.findByRole('checkbox', { name: /Системные операции/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить роль' }))
  expect(screen.getByRole('button', { name: 'Выдать привилегии' })).toBeDisabled()
  fireEvent.change(
    screen.getByLabelText('Введите ВЫДАТЬ ПРИВИЛЕГИИ field_lead для подтверждения'),
    { target: { value: 'ВЫДАТЬ ПРИВИЛЕГИИ field_lead' } },
  )
  fireEvent.click(screen.getByRole('button', { name: 'Выдать привилегии' }))
  await waitFor(() => expect(api.updateAdminRole).toHaveBeenCalledWith(
    customRole.id,
    expect.objectContaining({ permissions: ['system.manage'] }),
    customRole.revision,
  ))
})

it('does not offer deletion or deactivation for a system role', async () => {
  renderRolesPage([systemRole])
  await screen.findByText(systemRole.name)
  expect(screen.queryByRole('button', { name: /Удалить роль/ })).not.toBeInTheDocument()
  expect(screen.getByText('Системная роль')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run RED**

```bash
cd apps/web
npm test -- \
  src/domains/administration/roles/roleDraft.test.ts \
  src/domains/administration/roles/RolesAdminPage.test.tsx
```

Expected: FAIL because the role domain is absent.

- [ ] **Step 3: Implement role draft and permission grouping**

```ts
export type RoleDraft = {
  slug: string
  name: string
  description: string
  permissions: Set<string>
}

export function roleDraftToCreate(draft: RoleDraft) {
  return {
    slug: draft.slug.trim().toLowerCase(),
    name: draft.name.trim(),
    description: draft.description.trim(),
    permissions: [...draft.permissions].sort(),
  }
}

export function roleDraftToUpdate(draft: RoleDraft) {
  const { name, description, permissions } = roleDraftToCreate(draft)
  return { name, description, permissions }
}
```

Consume Task 6's additive `PermissionOut.is_privileged` / `PermissionCatalogItem.is_privileged` field. Group the server catalog by its `category` and sort by `sort_order`. Render server labels; never infer authorization or confirmation risk from a translated label/duplicated client key set. Slug is editable only while creating.

- [ ] **Step 4: Build a responsive master/detail role editor**

`?role=<id>` selects the role. Update only the `role` key in a cloned `URLSearchParams`, preserving Foundation's `park` key. Desktop shows list and editor side-by-side; mobile shows cards followed by the selected editor and a sticky save action. Each permission checkbox has the permission label and technical key. `user_count` and system status use textual `StatusBadge` content.

Create/update await server objects before changing the list. If the desired permission set adds any catalog item with `is_privileged`, create and update use `ConfirmDialog` with the exact `ВЫДАТЬ ПРИВИЛЕГИИ ${role.slug}` phrase before sending; removal does not reuse that copy. Delete is shown only when `actor.role === 'royal' && !role.is_system`; use `ConfirmDialog` with `confirmationPhrase={role.slug}` and pass `role.revision`. A `409 role_in_use` remains in the open dialog and explains the user count must be moved first. A `409 stale_revision` follows Task 5 recovery, keeps the role draft, loads current permissions visibly and requires a new save/phrase; it is never mislabeled `role_in_use`. A server `403` for a non-royal privileged-permission change leaves the saved role unchanged and renders a classified local error; the UI never treats its own role check as authorization.

- [ ] **Step 5: Register the route and run GREEN**

```ts
{
  id: 'admin-roles', path: '/admin/roles', label: 'Роли', icon: 'roles',
  permission: 'roles.manage', prerequisites: ['password-changed', 'approved'],
  surface: 'shell', nav: { group: 'administration', desktopOrder: 30 },
},
```

```tsx
'admin-roles': <RolesAdminPage />,
```

Run:

```bash
cd apps/web
npm test -- \
  src/domains/administration/roles/roleDraft.test.ts \
  src/domains/administration/roles/RolesAdminPage.test.tsx \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
```

Expected: all gates pass.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/domains/administration/roles \
  apps/api/src/robopark_api/routers/admin_roles.py \
  apps/api/tests/test_admin_roles.py \
  apps/web/src/api.ts \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/AppRouter.tsx
git commit -m "feat(web): add canonical role administration"
```

---

### Task 8: Deliver redacted integration administration

**Files:**

- Create: `apps/api/src/robopark_api/services/integration_diagnostics.py`
- Create: `apps/api/tests/test_integration_diagnostics.py`
- Modify: `apps/api/src/robopark_api/services/platform_settings.py`
- Modify: `apps/api/src/robopark_api/services/emergency_cache.py`
- Modify: `apps/api/src/robopark_api/services/emergency_keepalive.py`
- Modify: `apps/api/src/robopark_api/routers/admin_settings.py`
- Modify: `apps/api/tests/test_platform_settings.py`
- Modify: `apps/api/tests/test_tracker_policy_admin_settings.py`
- Modify: `apps/api/tests/test_emergency_cache.py`
- Modify: `apps/api/tests/test_emergency_keepalive.py`
- Modify: `apps/api/tests/test_emergency_cookie_report.py`
- Create: `apps/web/src/domains/administration/integrations/integrationStatus.ts`
- Create: `apps/web/src/domains/administration/integrations/integrationStatus.test.ts`
- Create: `apps/web/src/domains/administration/integrations/SecretReplacementForm.tsx`
- Create: `apps/web/src/domains/administration/integrations/IntegrationsAdminPage.tsx`
- Create: `apps/web/src/domains/administration/integrations/IntegrationsAdminPage.css`
- Create: `apps/web/src/domains/administration/integrations/IntegrationsAdminPage.test.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**

- Consumes: existing `/admin/settings/integrations`, tracker-token, emergency-cookie and tracker-policy APIs; existing `tracker_client.health_check`, `emergency_client.fetch_robot_payload`, keepalive ring/seed, and Task 2 settings revisions/audit.
- Produces additive `tracker_token_configured: bool`, `emergency_cookie_configured: bool`, `tracker_diagnostic`, and `robot_check_diagnostic`; `POST /admin/settings/integrations/tracker/check` and `/robot-check/check`; frontend `IntegrationStatusView`; route `admin-integrations` gated by `nav.admin`.
- Background cache/keepalive may maintain internal VIN freshness data but no longer mutates integration diagnostic/legacy-validity state or resolves cookie reports; only an explicit successful robot-check diagnostic does so.
- Compatibility: masked fields stay in the response for old clients but the new UI does not read or render them.

- [ ] **Step 1: Write backend and frontend redaction tests**

Extend `test_platform_settings.py`:

```python
def test_integration_status_exposes_flags_without_secret_values(client, seed_royal):
    login_as(client, "royal", "secret")
    token = "synthetic-integration-token"
    response = client.put("/admin/settings/tracker-token", json={"token": token})

    assert response.status_code == 200
    body = response.json()
    assert body["tracker_token_configured"] is True
    assert body["emergency_cookie_configured"] is False
    assert body["tracker_diagnostic"] == {
        "state": "unchecked", "checked_at": None, "reason": None,
    }
    assert token not in str(body)


def test_replacing_cookie_never_claims_it_is_valid_before_a_check(
    client, seed_royal
):
    login_as(client, "royal", "secret")
    before = client.get("/admin/settings/integrations").json()
    response = client.put(
        "/admin/settings/emergency-cookie",
        headers={"If-Match": before["revision"]},
        json={"cookie": "Session_id=synthetic-unchecked"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["emergency_cookie_valid"] is None
    assert body["robot_check_diagnostic"] == {
        "state": "unchecked", "checked_at": None, "reason": None,
    }
```

Create `test_integration_diagnostics.py`; monkeypatch all external probes so this suite never uses network or real credentials:

```python
from datetime import UTC, datetime

import pytest

from conftest import login_as
from robopark_api.services import integration_diagnostics, tracker_client


def test_tracker_check_persists_truthful_time_and_safe_result(
    client, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(integration_diagnostics, "utc_now", lambda: now)
    monkeypatch.setattr(integration_diagnostics, "probe_tracker", lambda token: True)
    configured = client.put(
        "/admin/settings/tracker-token",
        json={"token": "synthetic-token-never-return"},
    ).json()

    checked = client.post(
        "/admin/settings/integrations/tracker/check",
        headers={"If-Match": configured["revision"]},
    )

    assert checked.status_code == 200
    assert checked.json()["tracker_diagnostic"] == {
        "state": "healthy", "checked_at": "2026-09-02T12:00:00+00:00", "reason": None,
    }
    assert "synthetic-token-never-return" not in checked.text


@pytest.mark.parametrize(
    "raised",
    [
        tracker_client.TrackerError("synthetic-client-config-secret"),
        RuntimeError("synthetic-unbounded-probe-secret"),
    ],
)
def test_tracker_probe_exceptions_return_a_bounded_safe_diagnostic(
    client, seed_royal, monkeypatch, caplog, raised
):
    login_as(client, "royal", "secret")
    configured = client.put(
        "/admin/settings/tracker-token",
        json={"token": "synthetic-token-never-return"},
    ).json()

    def fail_probe(_token):
        raise raised

    monkeypatch.setattr(integration_diagnostics, "probe_tracker", fail_probe)
    checked = client.post(
        "/admin/settings/integrations/tracker/check",
        headers={"If-Match": configured["revision"]},
    )

    assert checked.status_code == 200
    assert checked.json()["tracker_diagnostic"]["state"] == "failing"
    assert checked.json()["tracker_diagnostic"]["reason"] == "probe_failed"
    assert "synthetic-client-config-secret" not in checked.text
    assert "synthetic-unbounded-probe-secret" not in checked.text
    assert "synthetic-client-config-secret" not in caplog.text
    assert "synthetic-unbounded-probe-secret" not in caplog.text


def test_robot_check_without_probe_robot_is_not_reported_healthy(
    client, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    monkeypatch.setattr(integration_diagnostics, "probe_robot_check", lambda _cookie, _vin: True)
    configured = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "Session_id=synthetic-cookie-never-return"},
    ).json()

    checked = client.post(
        "/admin/settings/integrations/robot-check/check",
        headers={"If-Match": configured["revision"]},
    )

    assert checked.status_code == 200
    assert checked.json()["robot_check_diagnostic"]["state"] == "failing"
    assert checked.json()["robot_check_diagnostic"]["reason"] == "probe_robot_missing"
    assert "synthetic-cookie-never-return" not in checked.text
```

`integrationStatus.test.ts`:

```ts
it('drops every masked field from the UI view model', () => {
  const view = toIntegrationStatusView({
    tracker_token_configured: true,
    tracker_token_masked: '•••• (25)',
    tracker_token_updated_at: '2026-09-02T10:00:00Z',
    tracker_token_encrypted: true,
    emergency_cookie_configured: false,
    emergency_cookie_masked: null,
    emergency_cookie_updated_at: null,
    emergency_cookie_encrypted: false,
    emergency_cookie_valid: null,
    revision: 'settings-4',
    tracker_diagnostic: {
      state: 'healthy', checked_at: '2026-09-02T10:04:00Z', reason: null,
    },
    robot_check_diagnostic: { state: 'unchecked', checked_at: null, reason: null },
  }, Date.parse('2026-09-02T10:10:01Z'))
  expect(view).toEqual({
    revision: 'settings-4',
    tracker: {
      configured: true, encrypted: true, updatedAt: '2026-09-02T10:00:00Z',
      health: 'stale', checkedAt: '2026-09-02T10:04:00Z', reason: null,
    },
    robotCheck: {
      configured: false, encrypted: false, updatedAt: null,
      health: 'not-configured', checkedAt: null, reason: null,
    },
  })
  expect(JSON.stringify(view)).not.toContain('••••')
})
```

`IntegrationsAdminPage.test.tsx` submits `synthetic-never-render`, opens replacement, proves the button remains disabled until `ЗАМЕНИТЬ TRACKER` is entered exactly, awaits success, then asserts the string is absent from `document.body.textContent` and `window.location.href`. Add a manual-check test that renders `Не проверено`, clicks `Проверить подключение Tracker`, then renders `Доступно · проверено 12:00`; advance the injected clock beyond five minutes and assert textual `Данные проверки устарели` plus `StaleBadge`. A failed check renders only a mapped safe reason and the server `checked_at`, never raw exception text.

- [ ] **Step 2: Run RED in both workspaces**

```bash
cd apps/api
uv run --frozen --extra dev pytest -q \
  tests/test_platform_settings.py \
  tests/test_integration_diagnostics.py
cd ../web
npm test -- \
  src/domains/administration/integrations/integrationStatus.test.ts \
  src/domains/administration/integrations/IntegrationsAdminPage.test.tsx
```

Expected: backend field assertions and absent frontend modules fail.

- [ ] **Step 3: Add truthful diagnostics without breaking legacy clients**

In `integration_diagnostics.py`, expose a small pure boundary around current clients:

```python
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from robopark_api.services import emergency_client, tracker_client

DiagnosticState = Literal["unchecked", "healthy", "failing"]
DiagnosticReason = Literal[
    "not_configured",
    "probe_robot_missing",
    "authentication_failed",
    "probe_failed",
]


@dataclass(frozen=True)
class DiagnosticResult:
    state: DiagnosticState
    checked_at: datetime
    reason: DiagnosticReason | None


def utc_now() -> datetime:
    return datetime.now(UTC)


def probe_tracker(token: str) -> bool:
    return tracker_client.health_check(token=token)


def probe_robot_check(cookie: str, vin: str) -> bool:
    emergency_client.fetch_robot_payload(cookie=cookie, vin=vin)
    return True


def check_tracker(token: str | None) -> DiagnosticResult:
    checked_at = utc_now()
    if not token:
        return DiagnosticResult("failing", checked_at, "not_configured")
    try:
        healthy = probe_tracker(token)
    except Exception:  # noqa: BLE001 -- diagnostic output must stay bounded and secret-safe
        return DiagnosticResult("failing", checked_at, "probe_failed")
    return DiagnosticResult(
        "healthy" if healthy else "failing",
        checked_at,
        None if healthy else "probe_failed",
    )


def check_robot(cookie: str | None, vin: str | None) -> DiagnosticResult:
    checked_at = utc_now()
    if not cookie:
        return DiagnosticResult("failing", checked_at, "not_configured")
    if not vin:
        return DiagnosticResult("failing", checked_at, "probe_robot_missing")
    try:
        probe_robot_check(cookie, vin)
    except emergency_client.EmergencyAuthError:
        return DiagnosticResult("failing", checked_at, "authentication_failed")
    except emergency_client.EmergencyError:
        return DiagnosticResult("failing", checked_at, "probe_failed")
    except Exception:  # noqa: BLE001 -- diagnostic output must stay bounded and secret-safe
        return DiagnosticResult("failing", checked_at, "probe_failed")
    return DiagnosticResult("healthy", checked_at, None)
```

Call `probe_tracker` exactly once per request. Its broad bounded catch intentionally mirrors the robot probe boundary: client construction/configuration failures and unexpected runtime exceptions all become the same safe `failing`/`probe_failed` result. No diagnostic function logs a token/cookie/exception or passes raw exception text into `reason`.

Add non-secret keys `tracker_diagnostic_state`, `tracker_diagnostic_checked_at`, `tracker_diagnostic_reason`, `robot_check_diagnostic_state`, `robot_check_diagnostic_checked_at`, and `robot_check_diagnostic_reason` in `platform_settings.py`. Implement `set_integration_diagnostic(db, integration: Literal["tracker", "robot_check"], result: DiagnosticResult, *, commit: bool = True)` through Task 2's commit-controllable setters. `integration_status` returns each diagnostic as `{ state, checked_at, reason }`, defaulting to unchecked/null/null when no check has run. The robot-check probe VIN is the newest keepalive-ring VIN, then the configured seed VIN, else `None`; neither VIN nor cookie is persisted in diagnostic keys.

Add this additive response model:

```python
class IntegrationDiagnosticOut(BaseModel):
    state: Literal["unchecked", "healthy", "failing"]
    checked_at: str | None
    reason: Literal[
        "not_configured",
        "probe_robot_missing",
        "authentication_failed",
        "probe_failed",
    ] | None
```

Add the two required configured booleans, `tracker_diagnostic`, `robot_check_diagnostic`, and Task 2 `revision` to `IntegrationSettingsOut` and `apps/web/src/api.ts` `IntegrationSettings`. Do not remove or rename any existing response field.

On token/cookie PUT, validate `If-Match` against the `integrations` group, write the secret with `commit=False`, delete that integration's three diagnostic keys with `commit=False`, bump the permanent `integrations` owner exactly once, add the required audit row and commit once. Cookie replacement also deletes `EMERGENCY_COOKIE_VALID_KEY`; it must never call `set_emergency_cookie_valid(..., True)`, and none of those ordinary-row deletes may touch the group owner. Clear caches only after commit. Remove legacy validity writes and `resolve_open_emergency_cookie_reports` calls from `emergency_cache.py` and `emergency_keepalive.py`: cache/keepalive traffic may still maintain its internal VIN ring/last-ok data, but it cannot mutate the admin integration status or resolve reports. Move report resolution exclusively to a verified successful robot-check diagnostic. Update the cache/keepalive/cookie-report tests to prove ordinary fetches do not change the diagnostic group token or close the report.

Implement both POST check routes. Each validates the `integrations` group token before probing, performs one read-only probe, saves only the result enum/time/safe reason, sets the legacy cookie-valid flag only for a completed robot-check probe (`healthy` → true, authentication/probe failure → false), bumps the group owner exactly once, attaches the matching Task 2 audit row and commits once. A missing token/cookie/probe VIN or a caught Tracker client/config exception returns HTTP 200 with truthful `failing` status and a safe reason because the diagnostic request itself completed. A stale `If-Match` returns `409 stale_revision` before probing.

- [ ] **Step 4: Create the redacted view boundary**

```ts
export type IntegrationStatusView = {
  revision: AdminRevision
  tracker: IntegrationItemView
  robotCheck: IntegrationItemView
}

export type IntegrationItemView = {
  configured: boolean
  encrypted: boolean
  updatedAt: string | null
  health: 'not-configured' | 'unchecked' | 'healthy' | 'failing' | 'stale'
  checkedAt: string | null
  reason: IntegrationDiagnosticReason | null
}

export const INTEGRATION_DIAGNOSTIC_STALE_MS = 5 * 60_000

export function toIntegrationStatusView(
  settings: IntegrationSettings,
  nowMs: number = Date.now(),
): IntegrationStatusView {
  const item = (
    configured: boolean,
    encrypted: boolean,
    updatedAt: string | null,
    diagnostic: IntegrationDiagnostic,
  ): IntegrationItemView => {
    const checkedMs = diagnostic.checked_at ? Date.parse(diagnostic.checked_at) : Number.NaN
    const hasValidCheck = Number.isFinite(checkedMs)
    const stale = hasValidCheck
      && nowMs - checkedMs > INTEGRATION_DIAGNOSTIC_STALE_MS
    return {
      configured,
      encrypted,
      updatedAt,
      health: !configured ? 'not-configured'
        : diagnostic.state === 'unchecked' || !hasValidCheck ? 'unchecked'
        : stale ? 'stale'
        : diagnostic.state,
      checkedAt: hasValidCheck ? diagnostic.checked_at : null,
      reason: diagnostic.reason,
    }
  }
  return {
    revision: settings.revision,
    tracker: item(
      settings.tracker_token_configured,
      Boolean(settings.tracker_token_encrypted),
      settings.tracker_token_updated_at,
      settings.tracker_diagnostic,
    ),
    robotCheck: item(
      settings.emergency_cookie_configured,
      Boolean(settings.emergency_cookie_encrypted),
      settings.emergency_cookie_updated_at,
      settings.robot_check_diagnostic,
    ),
  }
}
```

`IntegrationDiagnostic` and `IntegrationDiagnosticReason` in `api.ts` mirror the exact server literals. No UI module may accept `tracker_token_masked`, `emergency_cookie_masked`, or legacy `emergency_cookie_valid` as props. Invalid/missing timestamps are `unchecked`, not healthy or stale.

- [ ] **Step 5: Implement explicit secret replacement and policy controls**

`SecretReplacementForm` public interface:

```ts
export type SecretReplacementFormProps = {
  id: string
  label: string
  description: string
  configured: boolean
  encrypted: boolean
  updatedAt: string | null
  replacementPhrase: 'ЗАМЕНИТЬ TRACKER' | 'ЗАМЕНИТЬ ДОСТУП К ПРОВЕРКЕ'
  pending: boolean
  onReplace(value: string, revision: AdminRevision): Promise<void>
  revision: AdminRevision
}
```

The form stores the value only in component state, has `autoComplete="off"`, and opens a controlled confirmation with `confirmLabel="Заменить"` and `confirmationPhrase={replacementPhrase}`. `onConfirm` awaits `onReplace(value, revision)`, clears the value in `finally`, and closes only on success. Do not show a length mask or copy button.

`IntegrationsAdminPage` independently loads integration status and tracker policy. It renders:

- Tracker OAuth status and replacement form;
- “Доступ к данным проверки робота” cookie status and replacement form;
- for each integration: textual configured/encrypted state, last changed time, diagnostic status, `checkedAt`, safe reason and `Проверить подключение` action;
- policy switches for all four existing `TrackerPolicySettings` fields, each awaited server-side before changing the displayed saved state.

Map health to exact visible text: `not-configured` → `Не настроено`, `unchecked` → `Не проверено`, `healthy` → `Доступно`, `failing` → `Проверка не пройдена`, `stale` → `Данные проверки устарели`. Always render `checkedAt` when present; `stale` additionally uses Foundation `StaleBadge`. Manual checks use the current integration `revision`, disable only their own button, and replace state only from the returned server payload. Tracker policy save uses `ИЗМЕНИТЬ ПОЛИТИКУ TRACKER`. `409 stale_revision` follows Task 5 recovery; it does not retry a probe or secret replacement and always clears the secret/typed confirmation before loading current status.

The page may say “технический cookie сервиса” in explanatory copy but not “Emergency” as a product name.

- [ ] **Step 6: Register route, run GREEN and commit**

```ts
{
  id: 'admin-integrations', path: '/admin/integrations', label: 'Интеграции', icon: 'integration',
  permission: 'nav.admin', prerequisites: ['password-changed', 'approved'],
  surface: 'shell', nav: { group: 'administration', desktopOrder: 40 },
},
```

```tsx
'admin-integrations': <IntegrationsAdminPage />,
```

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q \
  tests/test_platform_settings.py \
  tests/test_integration_diagnostics.py \
  tests/test_tracker_policy_admin_settings.py \
  tests/test_emergency_cache.py \
  tests/test_emergency_keepalive.py \
  tests/test_emergency_cookie_report.py \
  tests/test_crypto_secrets.py
uv run --frozen --extra dev ruff check src/robopark_api/services/platform_settings.py \
  src/robopark_api/services/integration_diagnostics.py \
  src/robopark_api/services/emergency_cache.py \
  src/robopark_api/services/emergency_keepalive.py \
  src/robopark_api/routers/admin_settings.py \
  tests/test_platform_settings.py tests/test_integration_diagnostics.py \
  tests/test_tracker_policy_admin_settings.py tests/test_emergency_cache.py \
  tests/test_emergency_keepalive.py tests/test_emergency_cookie_report.py
cd ../web
npm test -- \
  src/domains/administration/integrations/integrationStatus.test.ts \
  src/domains/administration/integrations/IntegrationsAdminPage.test.tsx \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
cd ../..
git add apps/api/src/robopark_api/services/platform_settings.py \
  apps/api/src/robopark_api/services/integration_diagnostics.py \
  apps/api/src/robopark_api/services/emergency_cache.py \
  apps/api/src/robopark_api/services/emergency_keepalive.py \
  apps/api/tests/test_integration_diagnostics.py \
  apps/api/tests/test_tracker_policy_admin_settings.py \
  apps/api/tests/test_emergency_cache.py \
  apps/api/tests/test_emergency_keepalive.py \
  apps/api/tests/test_emergency_cookie_report.py \
  apps/api/src/robopark_api/routers/admin_settings.py \
  apps/api/tests/test_platform_settings.py \
  apps/web/src/api.ts apps/web/src/domains/administration/integrations \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/AppRouter.tsx apps/web/src/i18n/ru.ts
git commit -m "feat: add redacted integration administration"
```

Expected: all gates pass; a replacement is `unchecked` until a deterministic probe finishes; checked time/staleness is visible; synthetic secret text is absent from rendered output.

---

### Task 9: Deliver safety policy and the audit browser

**Files:**

- Create: `apps/web/src/domains/administration/shared/adminAuditActions.ts`
- Create: `apps/web/src/domains/administration/shared/AuditTrailLink.tsx`
- Create: `apps/web/src/domains/administration/shared/AuditTrailLink.test.tsx`
- Create: `apps/web/src/domains/administration/safety/auditLabels.ts`
- Create: `apps/web/src/domains/administration/safety/auditQuery.ts`
- Create: `apps/web/src/domains/administration/safety/auditQuery.test.ts`
- Create: `apps/web/src/domains/administration/safety/AuditEntryCard.tsx`
- Create: `apps/web/src/domains/administration/safety/AuditLogPanel.tsx`
- Create: `apps/web/src/domains/administration/safety/ScreenshotPolicyPanel.tsx`
- Create: `apps/web/src/domains/administration/safety/SafetyAdminPage.tsx`
- Create: `apps/web/src/domains/administration/safety/SafetyAdminPage.css`
- Create: `apps/web/src/domains/administration/safety/SafetyAdminPage.test.tsx`
- Modify: `apps/web/src/domains/administration/parks/ParksAdminPage.tsx`
- Modify: `apps/web/src/domains/administration/access/AccessAdminPage.tsx`
- Modify: `apps/web/src/domains/administration/roles/RolesAdminPage.tsx`
- Modify: `apps/web/src/domains/administration/integrations/IntegrationsAdminPage.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`

**Interfaces:**

- Consumes: `api.screenshotGuardSettings`, `updateScreenshotGuardSettings`, `auditActions`, `auditLog`; Phase 2 error classifier.
- Produces: `ADMIN_AUDIT_ACTION`, `AuditTrailLink`, `AuditQuery`, `parseAuditQuery`, `writeAuditQuery`; route `admin-safety` gated by `nav.admin`.

- [ ] **Step 1: Test URL filters, textual outcomes and server-confirmed toggles**

```ts
it('round-trips only supported audit filters', () => {
  const current = new URLSearchParams(
    'action=admin.user.delete&target=42&park=7&page=3&ignored=secret',
  )
  const parsed = parseAuditQuery(current)
  expect(parsed).toEqual({ action: 'admin.user.delete', target: '42', page: 3 })
  expect(writeAuditQuery(current, parsed).toString()).toBe(
    'park=7&action=admin.user.delete&target=42&page=3'
  )
  expect(toApiAuditParams(parsed, 7)).toEqual({
    action: 'admin.user.delete', target_id: '42', park_id: 7,
    limit: 50, offset: 100,
  })
})
```

`AuditTrailLink.test.tsx` pins permission and URL behavior:

```tsx
it('links an authorized actor to a safe filtered audit view', () => {
  renderAuditLink(royalUser, {
    action: ADMIN_AUDIT_ACTION.parkUpdate,
    targetId: '7',
    parkId: 7,
  })
  expect(screen.getByRole('link', { name: 'Открыть в аудите' })).toHaveAttribute(
    'href',
    '/admin/safety?park=7&action=admin.park.update&target=7',
  )
})

it('does not expose an audit link without access to admin safety', () => {
  renderAuditLink(parksManager, {
    action: ADMIN_AUDIT_ACTION.parkUpdate,
    targetId: '7',
    parkId: 7,
  })
  expect(screen.queryByRole('link', { name: 'Открыть в аудите' })).not.toBeInTheDocument()
})
```

```tsx
it('shows audit outcomes with text and not color alone', async () => {
  renderSafetyPage({ auditItems: [pendingEntry, failedEntry, successEntry] })
  expect(await screen.findByText('Результат уточняется')).toBeInTheDocument()
  expect(screen.getByText('Ошибка')).toBeInTheDocument()
  expect(screen.getByText('Успешно')).toBeInTheDocument()
})

it('keeps the old screenshot setting when the mutation fails', async () => {
  vi.spyOn(api, 'updateScreenshotGuardSettings').mockRejectedValue(new ApiError(500))
  renderSafetyPage({ screenshot: allDisabled })
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Оператор' }))
  expect(api.updateScreenshotGuardSettings).not.toHaveBeenCalled()
  fireEvent.change(
    screen.getByLabelText('Введите ИЗМЕНИТЬ ПОЛИТИКУ СКРИНШОТОВ для подтверждения'),
    { target: { value: 'ИЗМЕНИТЬ ПОЛИТИКУ СКРИНШОТОВ' } },
  )
  fireEvent.click(screen.getByRole('button', { name: 'Изменить политику' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('checkbox', { name: 'Оператор' })).not.toBeChecked()
})
```

- [ ] **Step 2: Run RED**

```bash
cd apps/web
npm test -- \
  src/domains/administration/shared/AuditTrailLink.test.tsx \
  src/domains/administration/safety/auditQuery.test.ts \
  src/domains/administration/safety/SafetyAdminPage.test.tsx
```

Expected: FAIL because the safety domain is absent.

- [ ] **Step 3: Implement a typed URL query and action labels**

Define every Task 2 value once on the frontend:

```ts
export const ADMIN_AUDIT_ACTION = {
  parkCreate: 'admin.park.create', parkUpdate: 'admin.park.update',
  parkRequestApprove: 'admin.park_request.approve',
  parkRequestReject: 'admin.park_request.reject',
  userCreate: 'admin.user.create', userUpdate: 'admin.user.update',
  userApprove: 'admin.user.approve', userReject: 'admin.user.reject',
  userDelete: 'admin.user.delete', roleCreate: 'admin.role.create',
  roleUpdate: 'admin.role.update', roleDelete: 'admin.role.delete',
  trackerToken: 'admin.settings.tracker_token',
  robotCheckCookie: 'admin.settings.emergency_cookie',
  trackerPolicy: 'admin.settings.tracker_policy',
  screenshotGuard: 'admin.settings.screenshot_guard',
  registrationPassword: 'admin.settings.registration_password',
  trackerCheck: 'admin.integration.tracker_check',
  robotCheckCheck: 'admin.integration.robot_check_check',
  sectionCreate: 'admin.robot_check.section.create',
  sectionUpdate: 'admin.robot_check.section.update',
  sectionReorder: 'admin.robot_check.section.reorder',
  sectionDelete: 'admin.robot_check.section.delete',
  fieldCreate: 'admin.robot_check.field.create',
  fieldUpdate: 'admin.robot_check.field.update',
  fieldDelete: 'admin.robot_check.field.delete',
  opsSnapshot: 'admin.ops.snapshot', opsRestore: 'admin.ops.restore',
  opsUpdate: 'admin.ops.update', opsAbort: 'admin.ops.abort',
} as const

export type AdminAuditAction = typeof ADMIN_AUDIT_ACTION[keyof typeof ADMIN_AUDIT_ACTION]
```

`AuditTrailLink` accepts `{ action: AdminAuditAction; targetId?: string; parkId?: number }`. It calls `canAccessRoute(user, 'admin-safety')`, returns `null` when inaccessible, and otherwise builds only `park`, `action`, and `target` into a `<Link>` labelled “Открыть в аудите”. It never accepts arbitrary search params.

```ts
export type AuditQuery = {
  action?: string
  target?: string
  page: number
}

export const AUDIT_PAGE_SIZE = 50

export function parseAuditQuery(params: URLSearchParams): AuditQuery {
  const parsedPage = Number(params.get('page'))
  return {
    action: params.get('action')?.trim() || undefined,
    target: params.get('target')?.trim() || undefined,
    page: Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1,
  }
}

export function writeAuditQuery(
  current: URLSearchParams,
  query: AuditQuery,
): URLSearchParams {
  const next = new URLSearchParams()
  const park = current.get(PARK_QUERY_KEY)
  if (park) next.set(PARK_QUERY_KEY, park)
  if (query.action) next.set('action', query.action)
  if (query.target) next.set('target', query.target)
  if (query.page > 1) next.set('page', String(query.page))
  return next
}

export function toApiAuditParams(query: AuditQuery, parkId: number | null) {
  return {
    action: query.action,
    target_id: query.target,
    park_id: parkId ?? undefined,
    limit: AUDIT_PAGE_SIZE,
    offset: (query.page - 1) * AUDIT_PAGE_SIZE,
  }
}
```

`parseAuditQuery` owns only `action`, `target`, and a positive integer `page`, defaulting page to 1. `writeAuditQuery(current, query)` creates a clean query in `park`/`action`/`target`/`page` order, copies the existing `PARK_QUERY_KEY` value without interpreting it, drops every unknown key, and omits page 1. Foundation `ParkScopeProvider` remains the sole validator/writer of `park`. `auditLabels.ts` maps every action from Task 2 to concise Russian text and falls back to the raw action string for forward compatibility.

After a successful mutation, update `ParksAdminPage`, `AccessAdminPage`, `RolesAdminPage`, and `IntegrationsAdminPage` success feedback to render `AuditTrailLink` with the exact matching `ADMIN_AUDIT_ACTION` value and known target ID; settings links use action only. The link appears only after the server response and never replaces the local success/error text.

- [ ] **Step 4: Build the audit browser as cards on every width**

`AuditLogPanel` reads its URL query and `parkId` from `useParkScope()`, then loads actions and the audit page independently through `toApiAuditParams(query, parkId)`. It exposes action/target filters, names the selected park scope beside them, and relies on the global park selector for switching parks; reset removes only action/target/page. Previous and next buttons use `page > 1` and the server `has_more` flag. `AuditEntryCard` always renders timestamp, action label, actor, target, outcome text and safe detail when present. Do not render client IP by default; put it behind a “Технические сведения” disclosure with an accessible name.

Map outcome to both label and `StatusTone`:

```ts
const OUTCOME = {
  pending: { label: 'Результат уточняется', tone: 'info' },
  success: { label: 'Успешно', tone: 'success' },
  failure: { label: 'Ошибка', tone: 'critical' },
  denied: { label: 'Отклонено политикой', tone: 'warning' },
} as const
```

Unknown outcome uses `{ label: entry.outcome, tone: 'neutral' }`.

- [ ] **Step 5: Build server-confirmed screenshot policy controls**

Render one labeled checkbox per `ScreenshotGuardSettings` key. A change remains a local proposed value and opens controlled `ConfirmDialog` with `confirmationPhrase="ИЗМЕНИТЬ ПОЛИТИКУ СКРИНШОТОВ"`; only its `onConfirm` calls `updateScreenshotGuardSettings({ [role]: next }, settings.revision)`. Replace the full saved settings only from the returned value. While pending, disable only the changed role. On failure preserve the old checked state and show classified `ErrorState`. On `stale_revision`, keep the proposed role/value, load current policy, present the changed role, clear the phrase and require a new explicit confirmation; do not toggle automatically.

Explain the real platform limitation: browser controls can discourage screenshots/copying but cannot reliably block OS-level capture on phones; watermark behavior remains owned by the existing ScreenshotGuard components.

- [ ] **Step 6: Register route, run GREEN and commit**

```ts
{
  id: 'admin-safety', path: '/admin/safety', label: 'Безопасность', icon: 'safety',
  permission: 'nav.admin', prerequisites: ['password-changed', 'approved'],
  surface: 'shell', nav: { group: 'administration', desktopOrder: 50 },
},
```

```tsx
'admin-safety': <SafetyAdminPage />,
```

Run:

```bash
cd apps/web
npm test -- \
  src/domains/administration/shared/AuditTrailLink.test.tsx \
  src/domains/administration/safety/auditQuery.test.ts \
  src/domains/administration/safety/SafetyAdminPage.test.tsx \
  src/components/ScreenshotGuard/screenshotGuardLogic.test.ts \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
git add src/domains/administration/safety \
  src/domains/administration/shared/adminAuditActions.ts \
  src/domains/administration/shared/AuditTrailLink.tsx \
  src/domains/administration/shared/AuditTrailLink.test.tsx \
  src/domains/administration/parks/ParksAdminPage.tsx \
  src/domains/administration/access/AccessAdminPage.tsx \
  src/domains/administration/roles/RolesAdminPage.tsx \
  src/domains/administration/integrations/IntegrationsAdminPage.tsx \
  src/app/routing/routeManifest.ts src/app/routing/AppRouter.tsx
git commit -m "feat(web): add safety policy and audit browser"
```

Expected: all gates pass and filters restore after reload through URL.

---

### Task 10: Deliver canonical robot-check configuration

**Files:**

- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/routers/admin_emergency.py`
- Modify: `apps/api/tests/test_admin_emergency.py`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/domains/administration/robot-check/robotCheckDraft.ts`
- Create: `apps/web/src/domains/administration/robot-check/robotCheckDraft.test.ts`
- Create: `apps/web/src/domains/administration/robot-check/RobotCheckFieldEditor.tsx`
- Create: `apps/web/src/domains/administration/robot-check/RobotCheckSectionCard.tsx`
- Create: `apps/web/src/domains/administration/robot-check/RobotCheckAdminPage.tsx`
- Create: `apps/web/src/domains/administration/robot-check/RobotCheckAdminPage.css`
- Create: `apps/web/src/domains/administration/robot-check/RobotCheckAdminPage.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/i18n/ru.ts`
- Delete: `apps/web/src/pages/AdminEmergencyConfig.tsx`

**Interfaces:**

- Consumes: existing `api.adminEmergencySections`, `api.adminRoles`, section/field CRUD, reorder and export methods; Task 9 `ADMIN_AUDIT_ACTION` and `AuditTrailLink`. Technical method names and `/admin/emergency/*` URLs remain unchanged.
- Produces: `SectionDraft`, `FieldDraft`, normalization helpers; canonical route `admin-robot-check` gated by `nav.admin`; robot-check viewer roles are validated active role slugs, not a closed system-role union.

- [ ] **Step 1: Write RED tests for terminology, selection and confirmations**

```ts
it('normalizes editable section data without mutating API objects', () => {
  const source = makeSection({ title: ' Статус ' })
  const draft = sectionToDraft(source)
  draft.roles.delete('driver')
  expect(sectionDraftToPatch(draft)).toEqual({
    title: 'Статус', is_enabled: true,
    roles: ['admin', 'mechanic', 'operator', 'royal'],
  })
  expect(source.roles).toContain('driver')
})
```

Add API RED coverage in `test_admin_emergency.py`: create an active custom role `field_lead`, POST a section with `roles: ['field_lead']`, and assert `201` plus that exact returned role. Then PATCH with an unknown slug and with an inactive role and assert `422` with `detail == 'invalid_emergency_viewer_roles'`, while the persisted valid roles remain unchanged. This proves a custom role granted `nav.emergency` can receive configured sections without allowing dangling/arbitrary role strings.

```tsx
it('uses only the approved product term in visible configuration copy', async () => {
  renderRobotCheckAdmin([statusSection])
  expect(await screen.findByRole('heading', { name: 'Конфигурация проверки робота' })).toBeInTheDocument()
  expect(document.body.textContent).not.toContain('Emergency')
})

it('confirms both field and section deletion through the shared dialog', async () => {
  renderRobotCheckAdmin([statusSection])
  fireEvent.click(await screen.findByRole('button', { name: 'Удалить поле VIN' }))
  expect(api.deleteEmergencyField).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Введите 1 для подтверждения'), {
    target: { value: '1' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Удалить поле' }))
  await waitFor(() => expect(api.deleteEmergencyField).toHaveBeenCalledWith(
    1, statusSection.fields[0].revision,
  ))

  fireEvent.click(screen.getByRole('button', { name: 'Удалить раздел Статус' }))
  fireEvent.change(screen.getByLabelText('Введите status для подтверждения'), {
    target: { value: 'status' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Удалить раздел' }))
  await waitFor(() => expect(api.deleteEmergencySection).toHaveBeenCalledWith(
    'status', statusSection.revision,
  ))
})
```

- [ ] **Step 2: Run RED**

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_admin_emergency.py

cd ../web
npm test -- \
  src/domains/administration/robot-check/robotCheckDraft.test.ts \
  src/domains/administration/robot-check/RobotCheckAdminPage.test.tsx
```

Expected: API custom-role cases fail against the closed `Literal`; web tests fail because the new domain is absent.

- [ ] **Step 3: Define immutable section/field drafts**

```ts
export type SectionDraft = {
  id: string
  title: string
  is_enabled: boolean
  roles: Set<string>
}

export type FieldDraft = { id: number; path: string; label: string }

export function sectionDraftToPatch(draft: SectionDraft) {
  return {
    title: draft.title.trim(),
    is_enabled: draft.is_enabled,
    roles: [...draft.roles].sort(),
  }
}

export function fieldDraftToPatch(draft: FieldDraft) {
  return { path: draft.path.trim(), label: draft.label.trim() }
}
```

Section ID remains immutable after creation and must match `[A-Za-z0-9_-]+`. Field path/label and title cannot be empty.

In `schemas.py`, replace the closed `EmergencyViewerRole` literal on admin create/update payloads with a reusable role-slug string constrained exactly like role creation: `2..32` characters and `^[a-z][a-z0-9_]*$`. Keep the public technical alias in TypeScript as `export type EmergencyViewerRole = string` (or use `User['role']` directly) because API/custom-role slugs remain raw strings throughout all plans. In `admin_emergency.py`, validate every submitted role in one query before mutating the section: every distinct slug must resolve to an active `Role`; otherwise raise `422` with `invalid_emergency_viewer_roles`. Validate before replacing `section.roles`, so a rejected PATCH cannot erase the current selection. Empty role arrays remain valid and intentionally hide the section from everyone.

- [ ] **Step 4: Split the existing behavior into responsive cards**

`RobotCheckAdminPage` owns the resource and URL `?section=<id>`. Its query writer replaces only `section` in a cloned `URLSearchParams`, preserving Foundation's `park` key and dropping no valid global scope. It shows create-section in a `Dialog`, export as `robot-check-sections.json`, and one selected section at a time below 900 px. Desktop may show the section list and editor together.

`RobotCheckSectionCard` supports title, enabled state, role toggles, ordered fields and move up/down buttons. `RobotCheckAdminPage` loads `api.adminRoles()` with the sections and renders every active role using its display name plus slug; no five-role constant is embedded in the UI. If a stored section references a now-inactive legacy role, show it as unavailable and selected so the next explicit save can remove it—never silently drop it while editing another field. Use explicit labels “Переместить раздел X выше/ниже”. Save/update/delete send the selected row revision; field creation sends its parent section revision; reorder sends `{ ids, expected_revisions }` for every visible section. Displayed state changes only from returned API rows.

Deleting a section uses danger `ConfirmDialog` with `confirmationPhrase={section.id}`. Deleting a field uses danger `ConfirmDialog` with `confirmationPhrase={String(field.id)}` and names section and field. Both keep errors in the open dialog. New fields use a native form and clear only after the returned field is incorporated.

On `stale_revision`, do not silently refetch away the section/field draft. Use Task 5 recovery to show the current server section beside retained changes; “Принять актуальную версию” or “Сохранить мой черновик” only changes local state/base revisions. Save/reorder/delete remains blocked until that decision, and delete requires its phrase again. Non-stale failures may refetch only after preserving a serializable local draft.

Every successful section/field create, save, reorder, or delete message includes `AuditTrailLink` with the corresponding Task 9 action and section/field target ID. Export is read-only and has no audit link.

- [ ] **Step 5: Add responsive styles and canonical route**

```css
.robot-check-admin { display: grid; grid-template-columns: minmax(15rem, 20rem) minmax(0, 1fr); gap: var(--rp-space-4); }
.robot-check-fields { display: grid; gap: var(--rp-space-3); }
.robot-check-field { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; gap: var(--rp-space-3); }
@media (max-width: 899px) { .robot-check-admin, .robot-check-field { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 599px) { .robot-check-section-actions { position: sticky; inset-block-end: 0; background: var(--rp-surface); } }
```

```ts
{
  id: 'admin-robot-check', path: '/admin/robot-check',
  legacyPaths: ['/admin/emergency/config'], label: 'Конфигурация проверки', icon: 'robot-check',
  permission: 'nav.admin', prerequisites: ['password-changed', 'approved'],
  surface: 'shell', nav: { group: 'administration', desktopOrder: 60 },
},
```

```tsx
'admin-robot-check': <RobotCheckAdminPage />,
```

- [ ] **Step 6: Remove the old page, run GREEN and commit**

Remove `AdminEmergencyConfig.tsx` after `AppRouter` uses the new page. Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_admin_emergency.py tests/test_emergency_sections.py
uv run --frozen --extra dev ruff check src/robopark_api/schemas.py \
  src/robopark_api/routers/admin_emergency.py tests/test_admin_emergency.py

cd ../web
npm test -- \
  src/domains/administration/robot-check/robotCheckDraft.test.ts \
  src/domains/administration/robot-check/RobotCheckAdminPage.test.tsx \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
rg -n "Emergency" src/domains/administration src/i18n/ru.ts
git diff --check
cd ../..
```

Expected: backend accepts only real active system/custom role slugs, all tests/gates pass, and search matches are limited to technical API identifiers/import types; no rendered strings contain `Emergency`.

```bash
git add apps/api/src/robopark_api/schemas.py \
  apps/api/src/robopark_api/routers/admin_emergency.py \
  apps/api/tests/test_admin_emergency.py \
  apps/web/src/api.ts \
  apps/web/src/domains/administration/robot-check \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/AppRouter.tsx \
  apps/web/src/i18n/ru.ts \
  apps/web/src/pages/AdminEmergencyConfig.tsx
git commit -m "feat(web): add robot-check configuration module"
```

---

### Task 11: Deliver high-risk system operations with exact confirmation

**Files:**

- Create: `apps/web/src/domains/administration/system/opsState.ts`
- Create: `apps/web/src/domains/administration/system/opsState.test.ts`
- Create: `apps/web/src/domains/administration/system/useOpsJobPolling.ts`
- Create: `apps/web/src/domains/administration/system/useOpsJobPolling.test.tsx`
- Create: `apps/web/src/domains/administration/system/OpsJobStatus.tsx`
- Create: `apps/web/src/domains/administration/system/SystemAdminPage.tsx`
- Create: `apps/web/src/domains/administration/system/SystemAdminPage.css`
- Create: `apps/web/src/domains/administration/system/SystemAdminPage.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**

- Consumes: existing `OpsJob` and `api.opsJob`, `opsAbort`, `opsSnapshot`, `opsArtifact`, `opsRestore`, `opsUpdate`; Task 9 `ADMIN_AUDIT_ACTION` and `AuditTrailLink`; Foundation `ConfirmDialog`.
- Produces: `isOpsActive(job)`, `opsStatusView(job)` and `useOpsJobPolling(): OpsPollingState`; route `admin-system` gated by `system.manage`. Task 1 makes every protected `/admin/ops/*` endpoint independently require the same effective permission; neither layer relies on a royal slug.

- [ ] **Step 1: Write state and dangerous-action tests**

`opsState.test.ts`:

```ts
it.each(['queued', 'running'])('treats %s as active', (state) => {
  expect(isOpsActive(makeJob({ state }))).toBe(true)
})

it.each(['idle', 'succeeded', 'failed', 'aborted'])('treats %s as inactive', (state) => {
  expect(isOpsActive(makeJob({ state }))).toBe(false)
})
```

`SystemAdminPage.test.tsx`:

```tsx
it('does not restore before the server phrase is entered exactly', async () => {
  renderSystemPage(makeJob({ restore_phrase: 'ВОССТАНОВИТЬ' }))
  chooseArchive('restore.zip')
  fireEvent.click(screen.getByRole('button', { name: 'Подготовить восстановление' }))
  expect(api.opsRestore).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Введите ВОССТАНОВИТЬ для подтверждения'), {
    target: { value: 'ВОССТАНОВИТЬ' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Восстановить систему' }))
  await waitFor(() => expect(api.opsRestore).toHaveBeenCalledWith(
    expect.any(File),
    'ВОССТАНОВИТЬ',
  ))
})

it('keeps a failed restore dialog open with the classified error', async () => {
  vi.spyOn(api, 'opsRestore').mockRejectedValue(new ApiError(400, 'invalid_zip'))
  renderSystemPage(idleJob)
  openAndConfirmRestore()
  expect(await screen.findByRole('alert')).toHaveTextContent('ZIP')
  expect(screen.getByRole('dialog', { name: 'Восстановить систему' })).toBeInTheDocument()
})

it('asks before aborting maintenance', async () => {
  renderSystemPage(runningJob)
  fireEvent.click(screen.getByRole('button', { name: 'Прервать операцию' }))
  expect(api.opsAbort).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Введите ПРЕРВАТЬ для подтверждения'), {
    target: { value: 'ПРЕРВАТЬ' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Прервать и снять техработы' }))
  await waitFor(() => expect(api.opsAbort).toHaveBeenCalledOnce())
})

it('requires the exact snapshot phrase before entering maintenance', async () => {
  renderSystemPage(idleJob)
  fireEvent.click(screen.getByRole('button', { name: 'Создать снимок' }))
  expect(api.opsSnapshot).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Введите СОЗДАТЬ СНИМОК для подтверждения'), {
    target: { value: 'СОЗДАТЬ СНИМОК' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Создать снимок системы' }))
  await waitFor(() => expect(api.opsSnapshot).toHaveBeenCalledOnce())
})
```

Define `chooseArchive` and `openAndConfirmRestore` in that test file using a synthetic `new File(['PK'], 'restore.zip', { type: 'application/zip' })`.

In `useOpsJobPolling.test.tsx`, use fake timers to pin the retry policy:

```tsx
it('backs off after an error and resets to one second after recovery', async () => {
  vi.useFakeTimers()
  vi.spyOn(api, 'opsJob')
    .mockResolvedValueOnce(runningJob)
    .mockRejectedValueOnce(new ApiError(503, 'temporary'))
    .mockResolvedValueOnce(runningJob)
  renderHook(() => useOpsJobPolling())
  await act(async () => Promise.resolve())
  expect(api.opsJob).toHaveBeenCalledTimes(1)

  await act(async () => vi.advanceTimersByTimeAsync(1_000))
  expect(api.opsJob).toHaveBeenCalledTimes(2)
  await act(async () => vi.advanceTimersByTimeAsync(1_999))
  expect(api.opsJob).toHaveBeenCalledTimes(2)
  await act(async () => vi.advanceTimersByTimeAsync(1))
  expect(api.opsJob).toHaveBeenCalledTimes(3)
  vi.useRealTimers()
})
```

- [ ] **Step 2: Run RED**

```bash
cd apps/web
npm test -- \
  src/domains/administration/system/opsState.test.ts \
  src/domains/administration/system/useOpsJobPolling.test.tsx \
  src/domains/administration/system/SystemAdminPage.test.tsx
```

Expected: FAIL because the system domain is absent.

- [ ] **Step 3: Define job presentation and visibility-aware polling**

```ts
export function isOpsActive(job: OpsJob | null): boolean {
  return job?.state === 'queued' || job?.state === 'running'
}

export function opsStatusView(job: OpsJob | null) {
  if (!job || job.state === 'idle' || !job.id) {
    return { label: 'Операций нет', tone: 'neutral' as const }
  }
  if (job.state === 'succeeded') return { label: 'Завершено', tone: 'success' as const }
  if (job.state === 'failed') return { label: 'Ошибка', tone: 'critical' as const }
  if (job.state === 'aborted') return { label: 'Прервано', tone: 'critical' as const }
  return { label: job.state === 'queued' ? 'В очереди' : 'Выполняется', tone: 'info' as const }
}
```

Use this public state:

```ts
export type OpsPollingState = {
  job: OpsJob | null
  loading: boolean
  refreshing: boolean
  checkedAt: Date | null
  error: DomainError | null
  refresh(): Promise<void>
}
```

`useOpsJobPolling` loads once on mount and exposes `refresh` for a visible “Обновить статус” action. While `isOpsActive(job)` and `document.visibilityState === 'visible'`, schedule the next request at `min(30_000, 1_000 * 2 ** consecutiveFailures)` ms: success resets failures to zero, error increments them. Cancel the timer when hidden/unmounted and trigger one immediate refresh on `visibilitychange` back to visible. A `404` becomes idle; other errors become `DomainError` without erasing the last job. `checkedAt` changes only after a successful response.

- [ ] **Step 4: Build isolated operation panels**

`SystemAdminPage` renders current job first, then three separate panels: snapshot, restore, update. Snapshot uses `confirmationPhrase="СОЗДАТЬ СНИМОК"`; abort uses `confirmationPhrase="ПРЕРВАТЬ"`. Restore and update require a selected `.zip` and use the phrases from the current `OpsJob`; fallback constants are exactly `ВОССТАНОВИТЬ` and `ОБНОВИТЬ`, matching current backend defaults.

For restore/update, `ConfirmDialog.onConfirm` sends the known required phrase prop to the API after the dialog validates user input. The page never reads the typed phrase back, never persists selected files, and clears each `File` reference after successful queueing. Success is represented only by the returned `OpsJob`.

Abort uses danger confirmation. Download creates an object URL, clicks a temporary anchor, and revokes the URL in `finally`. Job logs remain in `<pre>` with wrapping; request errors are passed through `classifyApiError`, preserving its mapped detail/request ID instead of rendering a raw exception.

After snapshot/restore/update/abort returns an `OpsJob`, show `AuditTrailLink` with its exact action and `targetId={job.id}` beside the server-confirmed status. Artifact download is read-only and has no mutation audit affordance.

- [ ] **Step 5: Add responsive styles and route**

```css
.system-ops-stack { display: grid; gap: var(--rp-space-4); }
.system-operation-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--rp-space-4); }
.system-job-log { max-block-size: 18rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 899px) { .system-operation-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 599px) { .system-operation-actions { position: sticky; inset-block-end: 0; background: var(--rp-surface); } }
```

```ts
{
  id: 'admin-system', path: '/admin/system', label: 'Система', icon: 'system',
  permission: 'system.manage', prerequisites: ['password-changed', 'approved'],
  surface: 'shell', nav: { group: 'administration', desktopOrder: 70 },
},
```

```tsx
'admin-system': <SystemAdminPage />,
```

- [ ] **Step 6: Run GREEN and commit**

```bash
cd apps/web
npm test -- \
  src/domains/administration/system/opsState.test.ts \
  src/domains/administration/system/useOpsJobPolling.test.tsx \
  src/domains/administration/system/SystemAdminPage.test.tsx \
  src/components/ops/maintenanceLogic.test.ts \
  src/app/routing/routeManifest.test.ts
npm run lint
npm run build
npm run check-nav
git diff --check
git add src/domains/administration/system \
  src/app/routing/routeManifest.ts src/app/routing/AppRouter.tsx src/i18n/ru.ts
git commit -m "feat(web): add confirmed system operations"
```

Expected: all gates pass; no operation starts before its dialog completes.

---

### Task 12: Cut over legacy admin routes and verify every Phase 4 screen

**Files:**

- Create: `apps/web/src/domains/administration/routing/LegacyAdminRedirect.tsx`
- Create: `apps/web/src/domains/administration/routing/LegacyAdminRedirect.test.tsx`
- Create: `apps/web/e2e/auth/onboarding.spec.ts`
- Create: `apps/web/e2e/auth/responsive-visual.spec.ts`
- Create: `apps/web/e2e/admin/modules.spec.ts`
- Create: `apps/web/e2e/admin/responsive-visual.spec.ts`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.test.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.test.ts`
- Modify: `apps/web/src/i18n/ru.ts`
- Delete: `apps/web/src/pages/Admin.tsx`
- Delete: `apps/web/src/components/admin/AdminUsersPanel.tsx`
- Delete: `apps/web/src/components/admin/AdminRolesPanel.tsx`
- Delete: `apps/web/src/components/admin/AdminOpsPanel.tsx`
- Delete: `apps/web/src/components/ui/AuthBrand.tsx`
- Delete: `apps/web/src/components/ui/PasswordField.tsx`
- Delete: `apps/web/src/components/ui/RolePicker.tsx`

**Interfaces:**

- Consumes: completed Phase 1–4 route IDs, `navigationForUser`, `canAccessRoute`, `ROUTE_MANIFEST`; Foundation `installMockApi` and `assertNoSeriousA11yViolations`.
- Reuses the Foundation compatibility route IDs `admin` and `admin-tracker`; produces exact compatibility routing and deterministic screenshots for every Phase 4 module.
- Does not remove legacy CSS selectors from `index.css`; that belongs to Phase 5 cleanup after all domains are migrated.

- [ ] **Step 1: Write legacy redirect tests before replacing `/admin`**

```tsx
it.each([
  ['/admin?tab=parks&park=7&token=synthetic', '/admin/parks?park=7'],
  ['/admin?tab=users', '/admin/access'],
  ['/admin?tab=roles', '/admin/roles'],
  ['/admin?tab=integrations', '/admin/integrations'],
  ['/admin?tab=ops', '/admin/system'],
  ['/admin/emergency/config?section=status&token=synthetic', '/admin/robot-check?section=status'],
  ['/admin/tracker?park=7&token=synthetic', '/work?park=7'],
  ['/operator/parks?token=synthetic', '/account?section=parks'],
])('redirects %s to %s without adding history', async (from, expected) => {
  const router = renderLegacyRoute(from, royalUser)
  await waitFor(() => expect(screen.getByTestId('current-location')).toHaveTextContent(expected))
  expect(router.state.historyAction).toBe('REPLACE')
})

it('falls back to the first accessible administration module', async () => {
  renderLegacyRoute('/admin?tab=ops', parksManager)
  await waitFor(() => expect(screen.getByTestId('current-location')).toHaveTextContent('/admin/parks'))
})
```

Update the cumulative independent `protectedRoutes` literal in `accessPolicy.test.ts`. Plans 02–03 already inserted their detail routes; Phase 4 must replace the three compatibility rows whose contracts change and append every new shell ID:

```ts
// Replace existing rows:
{ id: 'admin', permission: null, operatorOnly: false, mechanicPark: false },
{ id: 'admin-tracker', permission: null, operatorOnly: false, mechanicPark: false },
{ id: 'admin-robot-check', permission: 'nav.admin', operatorOnly: false, mechanicPark: false },

// Append new rows:
{ id: 'account', permission: null, operatorOnly: false, mechanicPark: false },
{ id: 'admin-parks', permission: 'parks.manage', operatorOnly: false, mechanicPark: false },
{ id: 'admin-access', permission: 'users.manage', operatorOnly: false, mechanicPark: false },
{ id: 'admin-roles', permission: 'roles.manage', operatorOnly: false, mechanicPark: false },
{ id: 'admin-integrations', permission: 'nav.admin', operatorOnly: false, mechanicPark: false },
{ id: 'admin-safety', permission: 'nav.admin', operatorOnly: false, mechanicPark: false },
{ id: 'admin-system', permission: 'system.manage', operatorOnly: false, mechanicPark: false },
```

Do not add a second, weaker Phase 4-only loop. Reuse the existing cumulative Cartesian tests across system/custom roles, approved/pending/rejected status, permission present/absent, forced password change, and mechanic park present/absent. Keep `operator-parks` as the existing operator-only compatibility row. The table remains hand-authored and must never derive permission or prerequisite values from `ROUTE_MANIFEST`.

Add direct `AppRouter.test.tsx` cases proving that a pending user loading `/admin/parks` is replaced to `/access/pending`, an approved user without `parks.manage` cannot render the parks heading and lands on their first accessible route, a forced-password user loading `/account` is replaced to `/change-password`, and approved `field_lead` with `parks.manage` can render `/admin/parks`. These route-gate checks prevent a correct pure matrix from being wired incorrectly in the router.

- [ ] **Step 2: Run legacy tests and confirm RED**

```bash
cd apps/web
npm test -- src/domains/administration/routing/LegacyAdminRedirect.test.tsx
```

Expected: FAIL because redirects are not implemented.

- [ ] **Step 3: Implement query-aware, permission-aware adapters**

Use this exact mapping:

```ts
const LEGACY_ADMIN_TAB: Readonly<Record<string, AppRouteId>> = {
  parks: 'admin-parks',
  users: 'admin-access',
  roles: 'admin-roles',
  integrations: 'admin-integrations',
  ops: 'admin-system',
}
```

`LegacyAdminRedirect` reads `tab`, accepts the mapped target only when `canAccessRoute(user, target)` is true, otherwise selects the first item returned by `navigationForUser(user, 'desktop')` whose `group === 'administration'`. If no admin module is accessible, use `landingPathForUser(user)`. Resolve the final path from the returned `NavigationItem.path` or the matching `ROUTE_MANIFEST` item.

Export this helper from the same module:

```ts
const LEGACY_QUERY_KEYS: Partial<Record<AppRouteId, readonly string[]>> = {
  admin: [PARK_QUERY_KEY],
  'admin-tracker': [PARK_QUERY_KEY],
  'admin-robot-check': [PARK_QUERY_KEY, 'section'],
}

export function safeLegacyAdminSearch(
  routeId: AppRouteId,
  current: URLSearchParams,
): string {
  const next = new URLSearchParams()
  for (const key of LEGACY_QUERY_KEYS[routeId] ?? []) {
    const value = current.get(key)
    if (value) next.set(key, value)
  }
  const serialized = next.toString()
  return serialized ? `?${serialized}` : ''
}
```

It copies values but does not validate `park`, leaving that to `ParkScopeProvider`. Render `<Navigate replace to={{ pathname: path, search: safeSearch }} />`; tests must assert the synthetic `token` key disappears.

Register non-nav compatibility entries:

```ts
{ id: 'admin', path: '/admin', label: 'Управление', icon: 'settings', prerequisites: ['password-changed', 'approved'], surface: 'shell' },
{ id: 'admin-tracker', path: '/admin/tracker', label: 'Работа', icon: 'work', prerequisites: ['password-changed', 'approved'], surface: 'shell' },
```

Map `admin` to `<LegacyAdminRedirect />` and `admin-tracker` to `<LegacyAdminTrackerRedirect />`, which uses the same safe-query helper and redirects with `replace` to `/work`. `/admin/emergency/config` remains in `admin-robot-check.legacyPaths`; update the `AppRouter` legacy alias branch to use `safeLegacyAdminSearch` for that route before rendering its replace redirect. Do not preserve unknown query keys on any of these adapters.

- [ ] **Step 4: Add deterministic auth/onboarding E2E**

Use Foundation `MockRoute.path` with the browser pathname including `/api`. The pending-to-approved case is stateful through a closure:

```ts
test('pending user manually refreshes into the overview', async ({ page }) => {
  let approved = false
  await installMockApi(page, {
    user: pendingUser,
    routes: [{
      method: 'GET',
      path: '/api/auth/me',
      handler: () => ({ json: approved ? approvedUser : pendingUser }),
    }],
  })
  await page.goto('/access/pending')
  await expect(page.getByRole('heading', { name: 'Ожидание доступа' })).toBeVisible()
  approved = true
  await page.getByRole('button', { name: 'Проверить статус' }).click()
  await expect(page).toHaveURL(/\/overview$/)
  await assertNoSeriousA11yViolations(page)
})
```

The same spec covers the exact `AUTH_HEADING` h1 values (including login `Вход`), login error/retry, all three registration role cards, visible password requirements, rejected truthful copy, mechanic no-park refresh, forced password change, no-cabinet, account theme choice, account density choice, and the `/operator/parks` → `/account?section=parks` journey followed by a synthetic successful park request. At 1440 px choose `Компактная`, reload `/account`, assert the radio remains selected and `data-density="compact"`; at 390 px assert the same saved preference remains selected while `data-density="comfortable"` and “На телефоне используется комфортная плотность” is visible. Each endpoint override returns synthetic users only.

- [ ] **Step 5: Add mocked admin journeys for every module**

In `modules.spec.ts`, install deterministic routes for every API consumed by Tasks 5–11. Use distinct synthetic identifiers and assert:

- `/admin/parks`: readiness reason/action, edit/approve, exact deactivation phrase and stale-draft recovery;
- `/admin/access`: exact approve/reject/deactivate/demote/password/delete phrases, privileged user create/update phrases, a single request after combined confirmations, password clearing after error/conflict, conflict recovery and registration gate redaction/phrases;
- `/admin/roles`: edit, privileged-grant phrase, delete phrase and distinct `role_in_use`/`stale_revision` recovery;
- `/admin/integrations`: configured-but-unchecked state, deterministic check time, healthy/failing/stale states, exact replacement/policy phrases and no rendered secret value;
- `/admin/safety`: confirmed screenshot mutation, stale policy recovery, pending/success/failure audit outcomes, filters and pagination;
- `/admin/robot-check`: section/field edit, revision-aware reorder, exact ID delete phrases, conflict recovery and approved terminology;
- `/admin/system`: exact snapshot/abort confirmations and restore/update phrases.

Every mocked permission-catalog item carries an explicit `is_privileged`; the access journey grants `system.manage` once during create and once during update so neither test can accidentally infer risk from a client-side key list.

Use closure mutation for one representative endpoint:

```ts
let parks: AdminPark[] = [{ ...northPark, revision: '1' }]
let injectConcurrentEdit = false
const parkRoutes: MockRoute[] = [
  { method: 'GET', path: '/api/parks', handler: () => ({ json: parks }) },
  {
    method: 'PATCH',
    path: /\/api\/parks\/\d+$/,
    handler: async (request) => {
      if (injectConcurrentEdit) {
        injectConcurrentEdit = false
        parks = [{ ...parks[0], name: 'Серверный вариант', revision: '2' }]
        return { status: 409, json: { detail: 'stale_revision' } }
      }
      if (request.headers.get('if-match') !== parks[0].revision) {
        return { status: 409, json: { detail: 'stale_revision' } }
      }
      const changes = await request.json() as Partial<Park>
      const revision = String(Number(parks[0].revision) + 1)
      parks = [{ ...parks[0], ...changes, revision }]
      return { json: parks[0] }
    },
  },
]
```

After the first park render, set `injectConcurrentEdit = true`, submit “Мой вариант”, assert the `409` recovery copy and retained input, load “Серверный вариант”, choose `Сохранить мой черновик`, and prove no second PATCH occurs until the explicit save. The second PATCH must carry `If-Match: 2`. This is a deterministic two-editor simulation, not a generic duplicate-tag conflict.

Never use a route handler that echoes a token, cookie or password response.

- [ ] **Step 6: Add responsive, theme, accessibility and visual regression matrices for admin and onboarding**

Both `admin/responsive-visual.spec.ts` and `auth/responsive-visual.spec.ts` import `AUTH_HEADING` from `src/domains/onboarding/authCopy.ts` and use the complete approved matrix, not a desktop-only representative:

```ts
const VIEWPORTS = [320, 390, 768, 1024, 1440] as const
const THEMES = ['light', 'dark'] as const
const ADMIN_CASES = [
  { name: 'parks', path: '/admin/parks' },
  { name: 'access', path: '/admin/access' },
  { name: 'roles', path: '/admin/roles' },
  { name: 'integrations', path: '/admin/integrations' },
  { name: 'safety', path: '/admin/safety' },
  { name: 'robot-check', path: '/admin/robot-check' },
  { name: 'system', path: '/admin/system' },
] as const
const ONBOARDING_CASES = [
  { name: 'login', path: '/login', heading: AUTH_HEADING.login, user: null },
  { name: 'register', path: '/register', heading: AUTH_HEADING.register, user: null },
  { name: 'change-password', path: '/change-password', heading: AUTH_HEADING.changePassword, user: forcedUser },
  { name: 'pending', path: '/access/pending', heading: 'Ожидание доступа', user: pendingUser },
  { name: 'rejected', path: '/access/rejected', heading: 'Доступ отклонён', user: rejectedUser },
  { name: 'no-park', path: '/mechanic/no-park', heading: 'Парк не назначен', user: mechanicWithoutPark },
  { name: 'no-cabinet', path: '/no-cabinet', heading: 'Кабинет недоступен', user: customRoleWithoutPermissions },
  { name: 'account', path: '/account', heading: 'Аккаунт', user: approvedOperator },
  { name: 'account-parks', path: '/account?section=parks', heading: 'Доступ к паркам', user: approvedOperator },
] as const
```

Define every named user next to the spec as a complete synthetic `User`. `customRoleWithoutPermissions.role` is `field_lead`, proving a Phase 4 custom slug safely reaches the truthful no-cabinet page. The auth visual fixture adds only these deterministic endpoints beyond Foundation defaults:

```ts
const parkAccessRoutes: MockRoute[] = [
  { method: 'GET', path: '/api/operator/available-parks', handler: () => ({ json: [southPark] }) },
  { method: 'GET', path: '/api/operator/park-requests', handler: () => ({ json: [] }) },
]
```

For every `VIEWPORTS × THEMES × ADMIN_CASES`, preseed `robopark-theme`, install `royalUser` plus the complete deterministic `adminRoutes`, load the path, assert an `h1`, assert `documentElement.dataset.theme === theme`, assert root overflow is at most one pixel, and run `assertNoSeriousA11yViolations`. The custom admin `/api/parks` and `/api/admin/park-requests` routes take precedence over Foundation defaults and return `AdminPark`/`AdminParkRequest` fixtures with revisions; shared onboarding fixtures remain ordinary `Park`/`ParkRequest`. For every `VIEWPORTS × THEMES × ONBOARDING_CASES`, do the same with that case's user, parks, and `parkAccessRoutes`, then assert its exact heading. This gives every authentication, registration, access-state, account/park-access, and administration screen both themes at 320/390/768/1024/1440.

At 390 and 1440 px, capture every case in both themes as `${case.name}-${width}-${theme}.png`. Admin cases switch Light/Dark through the real `Тема оформления` control before capture; public/standalone auth pages use the prepaint stored preference because they intentionally have no account control. Mask only fixed, explicitly named timestamp/job-log locators; do not mask layout defects. On 320 and 390, both specs call the same local touch-target assertion for every visible interactive element:

```ts
const undersized = await page.locator('button:visible, a:visible, input:visible, select:visible').evaluateAll(
  (nodes) => nodes.filter((node) => {
    const target = node.matches('input[type="checkbox"], input[type="radio"]')
      ? node.closest('label') ?? node
      : node
    const rect = target.getBoundingClientRect()
    return rect.width < 44 || rect.height < 44
  }).map((node) => node.getAttribute('aria-label') || node.textContent || node.tagName),
)
expect(undersized).toEqual([])
```

For text inputs/selects/textareas at widths through 899, additionally assert computed font size is at least 16 px; assert all other visible body/control text is at least 14 px. At 1024 and 1440, verify admin list/detail or form/action regions remain simultaneously reachable where the module owns both. The specs disable animations/caret in screenshots and contain no current timestamp or live service response.

- [ ] **Step 7: Remove old admin implementations and prove there is one live path**

After redirect tests and E2E are green, delete the seven old files listed in this task. Confirm no imports remain:

```bash
cd apps/web
rg -n "pages/Admin\.tsx|AdminUsersPanel|AdminRolesPanel|AdminOpsPanel|ui/AuthBrand|ui/PasswordField|ui/RolePicker" src
```

Expected: no matches. Do not delete `AdminTrackerWorkspace.tsx` here: Phase 4 only redirects it away from live routing, while Plan 05 removes the now-unreachable file together with the rest of the final legacy inventory.

- [ ] **Step 8: Run the complete Phase 4 verification**

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q \
  tests/test_admin_module_permissions.py \
  tests/test_admin_audit_coverage.py \
  tests/test_admin_revision_conflicts.py \
  tests/test_models_migration.py \
  tests/test_parks_extended.py \
  tests/test_park_requests.py \
  tests/test_access_requests.py \
  tests/test_admin_user_access.py \
  tests/test_admin_roles.py \
  tests/test_admin_emergency.py \
  tests/test_platform_settings.py \
  tests/test_registration_password_settings.py \
  tests/test_tracker_policy_admin_settings.py \
  tests/test_screenshot_guard_admin_settings.py \
  tests/test_integration_diagnostics.py \
  tests/test_emergency_cache.py \
  tests/test_emergency_keepalive.py \
  tests/test_emergency_cookie_report.py \
  tests/test_ops_http.py \
  tests/test_audit.py
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
uv run --frozen alembic heads
cd ../web
npm test
npm run lint
npm run build
npm run check-nav
npm run test:e2e -- e2e/auth/onboarding.spec.ts e2e/admin/modules.spec.ts
npm run test:e2e:update:linux -- e2e/auth/responsive-visual.spec.ts e2e/admin/responsive-visual.spec.ts
npm run test:e2e:linux -- e2e/auth/responsive-visual.spec.ts e2e/admin/responsive-visual.spec.ts
cd ../..
./scripts/verify.sh api
./scripts/verify.sh web
git diff --check
git status --short
```

Expected: API/unit/lint/format, web unit/lint/build/nav, Playwright journeys, axe and visual comparisons pass; `alembic heads` prints exactly `0017_admin_revisions (head)`, and `test_models_migration.py` proves the ephemeral `0015_report_attachments → 0016_report_idempotency → 0017_admin_revisions` upgrade chain. The update command creates the intentional first baselines; inspect their diff, then the following non-update run proves they are stable. `git status --short` shows only intended Phase 4 source/test/snapshot changes before commit. Docker verification remains governed by the Foundation/CI environment and is not silently claimed on a host without Docker.

- [ ] **Step 9: Commit the compatibility cutover and E2E evidence**

```bash
git add apps/web/src/domains/administration/routing \
  apps/web/e2e/auth/onboarding.spec.ts \
  apps/web/e2e/auth/responsive-visual.spec.ts \
  apps/web/e2e/auth/responsive-visual.spec.ts-snapshots \
  apps/web/e2e/admin/modules.spec.ts \
  apps/web/e2e/admin/responsive-visual.spec.ts \
  apps/web/e2e/admin/responsive-visual.spec.ts-snapshots \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/AppRouter.tsx \
  apps/web/src/app/routing/routeManifest.test.ts \
  apps/web/src/app/routing/accessPolicy.test.ts \
  apps/web/src/i18n/ru.ts \
  apps/web/src/pages/Admin.tsx \
  apps/web/src/components/admin/AdminUsersPanel.tsx \
  apps/web/src/components/admin/AdminRolesPanel.tsx \
  apps/web/src/components/admin/AdminOpsPanel.tsx \
  apps/web/src/components/ui/AuthBrand.tsx \
  apps/web/src/components/ui/PasswordField.tsx \
  apps/web/src/components/ui/RolePicker.tsx
git commit -m "feat(web): complete administration and onboarding cutover"
```

---

## Phase 4 acceptance checklist

- [ ] `/login`, `/register`, `/change-password`, `/access/pending`, `/access/rejected`, `/mechanic/no-park`, `/no-cabinet`, `/account` and `/account?section=parks` have deterministic unit/E2E plus light/dark 320/390/768/1024/1440 coverage.
- [ ] Approved operators retain assigned parks, request history and park-access requests inside `/account?section=parks`; `/operator/parks` is a lossless compatibility redirect and no duplicate screen remains live.
- [ ] Pending/no-park users can manually refresh without logout; the UI labels browser check time truthfully.
- [ ] All seven canonical admin URLs are manifest-driven, permission-filtered and present in `ROUTE_ELEMENTS`.
- [ ] `/admin` tab URLs, `/admin/emergency/config` and `/admin/tracker` redirect with `replace` and never bypass `AccessPolicy`.
- [ ] Every DB-backed admin mutation and its audit row commit atomically; ops persist a `pending` intent before filesystem side effects, keep queued/running asynchronous jobs pending, and finalize that same row from a fresh-session terminal worker or idempotent GET/reconcile fallback without ever preclaiming success. Abort success is recorded only after abort completes, and no audit detail contains raw `job.error`.
- [ ] Every admin mutation has an audit action; action filters include integration diagnostics and ops actions; audit detail never contains submitted secret values.
- [ ] Park cards distinguish inactive/missing-queue/filled configuration, name the concrete readiness reason and action, and never label configuration completeness as live integration health.
- [ ] Secret status is boolean/timestamp/encryption only; masked legacy values are not rendered. Replacement becomes `unchecked`; explicit diagnostics show safe result, real checked time and five-minute stale state.
- [ ] Stale admin revisions return `409 stale_revision`; every editor preserves non-secret draft context, loads current data and requires an explicit choice/resubmit. Shared Phase 1–3 `Park` and `ParkRequest` fixtures remain revision-free.
- [ ] Settings revisions come only from permanent monotonic group owners; every accepted group mutation bumps once, legacy missing owner token is `0`, reserved owners cannot be deleted, and an original token reused after delete/recreate returns `409 stale_revision`.
- [ ] Creating or updating a user with any newly effective/direct privileged permission requires the exact username-specific phrase once per combined save for royal, is not offered by non-royal UI, and remains server-authorized. User password state clears in `finally` on success, error and conflict.
- [ ] Tracker diagnostics convert client/configuration and unexpected probe exceptions to HTTP 200 `failing`/`probe_failed` without exposing exception text, logs, credentials or secrets.
- [ ] Every destructive/high-risk UI action uses Foundation `ConfirmDialog` with the canonical phrase table; no Phase 4 file calls `window.confirm`.
- [ ] Every list is cards or responsive master/detail below 900 px; all functionality remains available on phone.
- [ ] 320 px has no root horizontal overflow and visible interactive targets are at least 44×44 px.
- [ ] Light, dark and system themes preserve route/form context; reviewed Linux visual baselines cover every Phase 4 screen at 390 and 1440 px, while semantic overflow/touch/axe checks cover all five approved widths in both explicit themes.
- [ ] `/account` stores the Foundation desktop density preference, keeps `Компактная` selected on phone while truthfully resolving to comfortable, and never owns a second storage/state implementation.
- [ ] Auth routes render one exact h1 each: `Вход`, `Регистрация`, `Смена пароля`; product branding is not a second heading.
- [ ] User-facing copy says “Проверка робота”; technical Emergency identifiers remain backward-compatible.
- [ ] No real environment values, runtime data, tokens, cookies, passwords, ZIP contents or user payload entered tests, snapshots, logs or Git.
