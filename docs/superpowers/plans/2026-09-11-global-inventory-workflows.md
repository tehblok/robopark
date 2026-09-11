# Global Inventory Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести Robopark на единый каталог запчастей с отдельными остатками парков и дать механику быстрые поставки, инвентаризацию, поиск и выгрузку на телефоне и ПК.

**Architecture:** Глобальные catalog-таблицы отделяются от парковых stock-строк и неизменяемого журнала движений. Поставки и инвентаризации проводятся транзакционными документами через единый stock-сервис; старые inventory endpoint временно адаптируются к новой модели. React-раздел делится на пять URL-вкладок с общим ресурсным слоем и mechanic-first мобильной компоновкой.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, SQLite/PostgreSQL-compatible SQL, openpyxl, pytest, React 19, TypeScript, React Router, Vitest, Testing Library, Playwright, Vite.

**Spec:** `docs/superpowers/specs/2026-09-11-global-inventory-workflows-design.md`

## Global Constraints

- Каталог и фотографии глобальны; количество, минимум, место и документы принадлежат парку.
- Механик изменяет только свой парк и выгружает его; operator — доступные парки; admin/royal управляют глобальным каталогом и выгружают все парки.
- Остаток меняется только атомарным движением; повторное проведение идемпотентно.
- Физически не удалять каталог, движения и документы; использовать архивирование.
- Сохранить списание из взятой задачи и подпись Tracker от настроенного бота.
- На 320–599 px одновременно открыт один массивный редактор, touch target не меньше 44 px, горизонтального overflow нет.
- Миграция сохраняет остатки, места, фотографии и движения и стартует с `0025_local_task_claims`.
- Не читать и не коммитить `.release-secrets`, пользовательские untracked-файлы и runtime-данные.

## File Structure

- `models.py` and migration `0026` own persistence and legacy conversion only.
- `inventory_access.py` owns capabilities and park scope; domain services never reproduce role-name checks.
- `inventory_catalog.py`, `inventory_stock.py`, `inventory_receipts.py`, `inventory_counts.py`, and `inventory_exports.py` each own one business boundary.
- `inventory_schemas.py` and `routers/inventory.py` expose stable validation/HTTP mapping without business mutations.
- `InventoryPage.tsx` owns URL tabs and shared loading only; each view file owns one user workflow.
- `inventory.css` owns responsive layout; domain components keep semantic markup and accessible names.

---

### Task 1: Глобальная схема и безопасная миграция

**Files:**
- Create: `apps/api/alembic/versions/0026_global_inventory_workflows.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Test: `apps/api/tests/test_inventory_migration.py`
- Modify: `deploy/release-metadata.json`

**Interfaces:**
- Produces: `InventoryCatalogComponent`, `InventoryCatalogPart`, `InventoryParkStock`, `InventoryReceipt`, `InventoryReceiptLine`, `InventoryCount`, `InventoryCountLine`.
- Extends `InventoryMovement` with nullable `catalog_part_id`, `source_kind`, `source_id`, `balance_before`; retains legacy `part_id` during compatibility.
- `InventoryParkStock` is unique on `(park_id, catalog_part_id)`.

- [ ] **Step 1: Write failing migration tests**

Create equal articles in two parks with different quantities/locations and assert after upgrade:

```python
assert session.scalar(select(func.count(InventoryCatalogPart.id))) == 1
stocks = session.scalars(select(InventoryParkStock).order_by(InventoryParkStock.park_id)).all()
assert [(row.quantity, row.location) for row in stocks] == [(2, "A-1"), (7, "B-4")]
assert set(session.scalars(select(InventoryMovement.catalog_part_id))) == {catalog_part.id}
```

Add conflicting names/components for one article and assert deterministic earliest metadata plus an `inventory_migration_conflicts` row.

- [ ] **Step 2: Run failure**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory_migration.py -q`

Expected: FAIL because revision `0026` and catalog models are absent.

- [ ] **Step 3: Implement models and migration**

```python
def normalize_inventory_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())
```

Deduplicate non-empty normalized articles, create park stock rows, remap movements, retain earliest photo metadata, record conflicts and keep legacy tables. Set metadata head to `0026_global_inventory_workflows` with `0025_local_task_claims` in `from_heads`.

- [ ] **Step 4: Verify migration**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory_migration.py tests/test_migrations.py -q`

Expected: PASS; downgrade leaves legacy tables readable.

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0026_global_inventory_workflows.py apps/api/src/robopark_api/models.py apps/api/tests/test_inventory_migration.py deploy/release-metadata.json
git commit -m "feat: add global inventory schema"
```

### Task 2: Права, каталог, поиск и остатки

**Files:**
- Create: `apps/api/src/robopark_api/services/inventory_access.py`
- Create: `apps/api/src/robopark_api/services/inventory_catalog.py`
- Create: `apps/api/src/robopark_api/services/inventory_stock.py`
- Modify: `apps/api/src/robopark_api/services/rbac.py`
- Modify: `apps/api/src/robopark_api/inventory_schemas.py`
- Modify: `apps/api/src/robopark_api/routers/inventory.py`
- Modify: `apps/api/src/robopark_api/services/inventory.py`
- Test: `apps/api/tests/test_inventory.py`
- Create: `apps/api/tests/test_inventory_catalog.py`

**Interfaces:**
- Produces permissions: `inventory.stock.manage`, `inventory.documents.post`, `inventory.export`, `inventory.catalog.manage`.
- Produces `search_catalog(db, user, *, park_id, query, component_id, stock_filter, limit, offset)`.
- Produces `ensure_stock(db, *, park_id, catalog_part_id)` and `apply_stock_delta(..., delta, source_kind, source_id, note)`.
- Preserves legacy `overview`, `create_part`, `update_part`, `move_stock`, `task_writeoff` as adapters.

- [ ] **Step 1: Write failing role/search/stock tests**

```python
response = mechanic_client.get("/inventory/catalog/search", params={"park_id": own.id, "q": "abc"})
assert response.status_code == 200
assert response.json()["items"][0]["quantity"] == 0
assert mechanic_client.get("/inventory/catalog/search", params={"park_id": foreign.id}).status_code == 403
assert admin_client.patch(f"/inventory/catalog/parts/{part.id}", json={"name": "Исправлено"}).status_code == 200
assert mechanic_client.patch(f"/inventory/catalog/parts/{part.id}", json={"name": "Нет"}).status_code == 403
```

Cover insufficient quantity, signed adjustment, duplicate article and mismatched park/part IDs.

- [ ] **Step 2: Run failure**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory.py tests/test_inventory_catalog.py -q`

Expected: new endpoints return 404.

- [ ] **Step 3: Implement services and routes**

`inventory_access.py` owns capability/scope, `inventory_catalog.py` owns normalized global CRUD/search, and `inventory_stock.py` is the only quantity writer:

```python
before = stock.quantity
if before + delta < 0:
    raise InventoryConflict("inventory_out_of_stock", current_quantity=before)
stock.quantity = before + delta
movement = InventoryMovement(balance_before=before, balance_after=stock.quantity, delta=delta, **source)
```

Lock stock with `with_for_update`, map `InventoryConflict` to HTTP 409, paginate and sort by normalized name/article/ID.

- [ ] **Step 4: Verify compatibility**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory.py tests/test_inventory_catalog.py tests/test_actor_lifecycle.py -q`

Expected: PASS; task writeoff updates new stock and keeps the bot comment.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/services/inventory_access.py apps/api/src/robopark_api/services/inventory_catalog.py apps/api/src/robopark_api/services/inventory_stock.py apps/api/src/robopark_api/services/rbac.py apps/api/src/robopark_api/inventory_schemas.py apps/api/src/robopark_api/routers/inventory.py apps/api/src/robopark_api/services/inventory.py apps/api/tests/test_inventory.py apps/api/tests/test_inventory_catalog.py
git commit -m "feat: add shared inventory catalog"
```

### Task 3: Документы поставок

**Files:**
- Create: `apps/api/src/robopark_api/services/inventory_receipts.py`
- Modify: `apps/api/src/robopark_api/inventory_schemas.py`
- Modify: `apps/api/src/robopark_api/routers/inventory.py`
- Create: `apps/api/tests/test_inventory_receipts.py`

**Interfaces:**
- Produces `create_receipt`, `update_receipt`, `list_receipts`, `post_receipt`, `cancel_receipt`.
- API: `GET|POST /inventory/parks/{park_id}/receipts`, `PATCH /.../{receipt_id}`, `POST /.../{receipt_id}/post|cancel`.
- `post_receipt` uses Task 2 `apply_stock_delta(..., source_kind='receipt')`.

- [ ] **Step 1: Write failing receipt lifecycle test**

```python
created = client.post(f"/inventory/parks/{park.id}/receipts", json={
    "supplier": "Завод", "document_number": "UPD-42", "received_on": "2026-09-11",
    "lines": [{"catalog_part_id": part.id, "quantity": 5}],
}).json()
assert client.post(f"/inventory/parks/{park.id}/receipts/{created['id']}/post").json()["status"] == "posted"
assert stock_quantity(db, park.id, part.id) == 5
client.post(f"/inventory/parks/{park.id}/receipts/{created['id']}/post")
assert movement_count(db, source_kind="receipt", source_id=created["id"]) == 1
```

Cover empty/duplicate lines, foreign park, edit-after-post and cancel.

- [ ] **Step 2: Run failure**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory_receipts.py -q`

Expected: endpoint 404.

- [ ] **Step 3: Implement transactional receipts**

Combine duplicate lines, lock the receipt before posting, return an already-posted receipt on retry, and commit movements/status atomically. Correction uses a separate reversal with mandatory reason.

- [ ] **Step 4: Verify receipts**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory_receipts.py tests/test_inventory_catalog.py -q`

Expected: PASS, including idempotent double post.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/services/inventory_receipts.py apps/api/src/robopark_api/inventory_schemas.py apps/api/src/robopark_api/routers/inventory.py apps/api/tests/test_inventory_receipts.py
git commit -m "feat: add inventory receipts"
```

### Task 4: Инвентаризационные акты

**Files:**
- Create: `apps/api/src/robopark_api/services/inventory_counts.py`
- Modify: `apps/api/src/robopark_api/inventory_schemas.py`
- Modify: `apps/api/src/robopark_api/routers/inventory.py`
- Create: `apps/api/tests/test_inventory_counts.py`

**Interfaces:**
- Produces `create_count`, `update_count_lines`, `list_counts`, `post_count`, `cancel_count`.
- API: `GET|POST /inventory/parks/{park_id}/counts`, `PATCH /.../{count_id}`, `POST /.../{count_id}/post|cancel`.
- `post_count` applies `actual_quantity - expected_quantity` with `source_kind='count'`.

- [ ] **Step 1: Write failing count and conflict tests**

```python
count = client.post(f"/inventory/parks/{park.id}/counts", json={
    "name": "Сентябрь", "scope": {"kind": "component", "component_id": component.id}
}).json()
line = count["lines"][0]
client.patch(f"/inventory/parks/{park.id}/counts/{count['id']}", json={
    "lines": [{"catalog_part_id": line["catalog_part_id"], "actual_quantity": 3}]
})
assert client.post(f"/inventory/parks/{park.id}/counts/{count['id']}/post").status_code == 200
assert stock_quantity(db, park.id, line["catalog_part_id"]) == 3
```

Mutate stock after count creation and assert HTTP 409 lists every conflict and creates no movement.

- [ ] **Step 2: Run failure**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py -q`

Expected: endpoint 404.

- [ ] **Step 3: Implement count lifecycle**

Snapshot expected quantities, accept non-negative actual values, lock stocks in sorted part-ID order, detect all stale lines before any mutation, then apply signed deltas atomically. Keep zero-delta lines without movements.

- [ ] **Step 4: Verify counts**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py tests/test_inventory_receipts.py -q`

Expected: PASS and conflicts leave stock/document unchanged.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/services/inventory_counts.py apps/api/src/robopark_api/inventory_schemas.py apps/api/src/robopark_api/routers/inventory.py apps/api/tests/test_inventory_counts.py
git commit -m "feat: add inventory counts"
```

### Task 5: CSV/XLSX-выгрузки

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Create: `apps/api/src/robopark_api/services/inventory_exports.py`
- Modify: `apps/api/src/robopark_api/routers/inventory.py`
- Create: `apps/api/tests/test_inventory_exports.py`

**Interfaces:**
- Produces `build_inventory_export(db, user, *, park_id, all_parks, format) -> tuple[str, str, Iterator[bytes]]`.
- API: `GET /inventory/export?park_id={id}&format=csv|xlsx` and `?scope=all&format=csv|xlsx`.
- Adds pinned `openpyxl==3.1.5` dependency.

- [ ] **Step 1: Write failing export/security tests**

```python
csv_result = mechanic_client.get("/inventory/export", params={"park_id": own.id, "format": "csv"})
assert csv_result.status_code == 200
assert csv_result.content.startswith(b"\xef\xbb\xbf")
assert mechanic_client.get("/inventory/export", params={"scope": "all", "format": "xlsx"}).status_code == 403
xlsx_result = royal_client.get("/inventory/export", params={"scope": "all", "format": "xlsx"})
book = load_workbook(BytesIO(xlsx_result.content), read_only=True)
assert book.sheetnames == ["Остатки", "Движения", "Поставки", "Инвентаризации"]
```

Add a formula-like article and assert CSV/XLSX store it as inert text.

- [ ] **Step 2: Run failure**

Run: `cd apps/api && .venv/bin/pytest tests/test_inventory_exports.py -q`

Expected: endpoint 404.

- [ ] **Step 3: Implement bounded export**

Use `csv.writer` plus UTF-8 BOM and openpyxl write-only sheets. Prefix text beginning with `=`, `+`, `-`, `@` with an apostrophe, cap each export at 100,000 rows, and return `StreamingResponse` with safe ASCII and RFC 5987 filenames. Check scope before querying.

- [ ] **Step 4: Verify export**

Run: `cd apps/api && uv sync --frozen && .venv/bin/pytest tests/test_inventory_exports.py tests/test_inventory.py -q`

Expected: PASS and both formats contain only authorized parks.

- [ ] **Step 5: Commit**

```bash
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/src/robopark_api/services/inventory_exports.py apps/api/src/robopark_api/routers/inventory.py apps/api/tests/test_inventory_exports.py
git commit -m "feat: export park inventory"
```

### Task 6: Web-контракты и пять URL-вкладок

**Files:**
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/domains/inventory/inventoryTypes.ts`
- Create: `apps/web/src/domains/inventory/InventoryTabs.tsx`
- Create: `apps/web/src/domains/inventory/InventoryTabs.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryPage.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`
- Test: `apps/web/src/domains/inventory/InventoryPage.test.tsx`

**Interfaces:**
- Produces tab IDs `parts|receipts|counts|manage|export` in query parameter `view`.
- Produces API types `InventoryCatalogPart`, `InventoryStockView`, `InventoryReceipt`, `InventoryCount` and methods matching Tasks 2–5.
- `InventoryPage` becomes a shell that mounts only the active workflow.

- [ ] **Step 1: Write failing URL/role/mobile tests**

```tsx
renderInventory('/inventory?park=1&view=receipts', mechanic)
expect(await screen.findByRole('tab', { name: 'Поставки' })).toHaveAttribute('aria-selected', 'true')
await user.click(screen.getByRole('tab', { name: 'Инвентаризация' }))
expect(screen.getByLabelText('Адрес')).toHaveTextContent('view=counts')
expect(screen.queryByText('Все парки')).not.toBeInTheDocument()
```

At 390 px assert the tablist is keyboard-operable and the page does not overflow.

- [ ] **Step 2: Run failure**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryTabs.test.tsx src/domains/inventory/InventoryPage.test.tsx`

Expected: no URL tabs or new API contracts.

- [ ] **Step 3: Implement shell/contracts**

Use shared ARIA tabs, preserve `park`, replace only `view`, default invalid values to `parts`, and keep KPI/park identity above tabs. Add authenticated download that validates content type, clicks an object URL and revokes it in `finally`.

- [ ] **Step 4: Verify shell**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryTabs.test.tsx src/domains/inventory/InventoryPage.test.tsx`

Expected: PASS for mechanic, operator, admin and royal fixtures.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/domains/inventory/inventoryTypes.ts apps/web/src/domains/inventory/InventoryTabs.tsx apps/web/src/domains/inventory/InventoryTabs.test.tsx apps/web/src/domains/inventory/InventoryPage.tsx apps/web/src/domains/inventory/inventory.css apps/web/src/domains/inventory/InventoryPage.test.tsx
git commit -m "feat: add inventory workflow tabs"
```

### Task 7: Mechanic-first поиск и управление позициями

**Files:**
- Create: `apps/web/src/domains/inventory/InventoryPartsView.tsx`
- Create: `apps/web/src/domains/inventory/InventoryPartsView.test.tsx`
- Create: `apps/web/src/domains/inventory/InventoryManageView.tsx`
- Create: `apps/web/src/domains/inventory/InventoryManageView.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryLabels.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`

**Interfaces:**
- Consumes Task 6 API/shell.
- Produces debounced `{q, componentId, stockFilter, limit, offset}` search and one selected `catalogPartId`.
- Mechanics add catalog items and edit own stock; admin/royal also edit/archive/merge global items.

- [ ] **Step 1: Write failing search/permission tests**

```tsx
await user.type(screen.getByRole('searchbox', { name: 'Найти запчасть' }), 'ABC')
await waitFor(() => expect(api.searchInventory).toHaveBeenCalledWith(expect.objectContaining({ parkId: 1, query: 'ABC' })))
expect(await screen.findByText('Полка A-1')).toBeVisible()
expect(screen.getByText('5 шт.')).toBeVisible()
expect(screen.queryByRole('button', { name: 'Архивировать глобально' })).not.toBeInTheDocument()
```

Rerender as royal and assert global edit/archive/merge controls. On 390 px assert only one movement/edit/label form mounts.

- [ ] **Step 2: Run failure**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryPartsView.test.tsx src/domains/inventory/InventoryManageView.test.tsx`

Expected: components absent.

- [ ] **Step 3: Implement search cards and scoped management**

Place one search field before filters. Cards always show <=72 px photo, name, article, quantity and location. Use request generation IDs so slow results cannot replace newer ones. On `inventory_article_exists`, select the existing part and open park settings while retaining the draft.

- [ ] **Step 4: Verify parts/manage**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryPartsView.test.tsx src/domains/inventory/InventoryManageView.test.tsx src/domains/inventory/InventoryPage.test.tsx`

Expected: PASS and no global destructive action for mechanic/operator.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/inventory/InventoryPartsView.tsx apps/web/src/domains/inventory/InventoryPartsView.test.tsx apps/web/src/domains/inventory/InventoryManageView.tsx apps/web/src/domains/inventory/InventoryManageView.test.tsx apps/web/src/domains/inventory/InventoryLabels.tsx apps/web/src/domains/inventory/inventory.css
git commit -m "feat: add fast inventory search"
```

### Task 8: Поставки и инвентаризация в web

**Files:**
- Create: `apps/web/src/domains/inventory/InventoryReceiptsView.tsx`
- Create: `apps/web/src/domains/inventory/InventoryReceiptsView.test.tsx`
- Create: `apps/web/src/domains/inventory/InventoryCountsView.tsx`
- Create: `apps/web/src/domains/inventory/InventoryCountsView.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryPage.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`

**Interfaces:**
- Consumes Task 6 API and Task 7 catalog picker.
- Produces one selected receipt/count editor; server IDs/statuses remain authoritative.
- Browser draft stores only unsent form fields.

- [ ] **Step 1: Write failing workflow tests**

```tsx
await user.click(screen.getByRole('button', { name: 'Новая поставка' }))
await user.type(screen.getByLabelText('Поставщик или завод'), 'Завод')
await addPartLine(user, 'ABC-1', 5)
await user.click(screen.getByRole('button', { name: 'Провести поставку' }))
expect(api.postInventoryReceipt).toHaveBeenCalledTimes(1)
expect(await screen.findByText('Поставка проведена')).toBeVisible()
```

For count, assert actual quantities, differences, conflict highlighting, retained input and retry.

- [ ] **Step 2: Run failure**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryReceiptsView.test.tsx src/domains/inventory/InventoryCountsView.test.tsx`

Expected: views absent.

- [ ] **Step 3: Implement document workflows**

Desktop uses list/detail columns; phone uses list or one document with Back. Add lines through catalog search, combine duplicates, confirm post/cancel, disable while pending and map 409 conflicts beside affected lines. On success invalidate overview/search/movement/document resources.

- [ ] **Step 4: Verify documents UI**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryReceiptsView.test.tsx src/domains/inventory/InventoryCountsView.test.tsx src/domains/inventory/InventoryPage.test.tsx`

Expected: PASS, including phone back and double-click protection.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/inventory/InventoryReceiptsView.tsx apps/web/src/domains/inventory/InventoryReceiptsView.test.tsx apps/web/src/domains/inventory/InventoryCountsView.tsx apps/web/src/domains/inventory/InventoryCountsView.test.tsx apps/web/src/domains/inventory/InventoryPage.tsx apps/web/src/domains/inventory/inventory.css
git commit -m "feat: add inventory document workflows"
```

### Task 9: Выгрузка, park scope и списание из задачи

**Files:**
- Create: `apps/web/src/domains/inventory/InventoryExportView.tsx`
- Create: `apps/web/src/domains/inventory/InventoryExportView.test.tsx`
- Modify: `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
- Test: `apps/web/src/domains/inventory/TaskPartsPanel.test.tsx`
- Modify: `apps/web/src/app/park/ParkScopeProvider.tsx`
- Test: `apps/web/src/app/park/ParkScopeProvider.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryPage.tsx`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**
- Mechanic export is fixed to own park; operator exports selected park; admin/royal select one or all.
- Task picker uses a global catalog part ID plus issue-park stock while preserving Tracker message fields.
- `/inventory` exposes fleet selection to admin/royal/operator and keeps mechanic park-locked.

- [ ] **Step 1: Write failing export/scope/task tests**

```tsx
renderExport(mechanic)
expect(screen.getByText('Выгрузка парка Next')).toBeVisible()
expect(screen.queryByRole('option', { name: 'Все парки' })).not.toBeInTheDocument()
await user.click(screen.getByRole('button', { name: 'Скачать Excel' }))
expect(api.downloadInventoryExport).toHaveBeenCalledWith({ parkId: 1, format: 'xlsx' })
```

Select a global part stocked only in the issue park and assert task writeoff uses catalog ID and displays that park's location.

- [ ] **Step 2: Run failure**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryExportView.test.tsx src/domains/inventory/TaskPartsPanel.test.tsx src/app/park/ParkScopeProvider.test.tsx`

Expected: export view absent and legacy picker expects park-local IDs.

- [ ] **Step 3: Implement scoped export and compatibility**

Show all-parks only when the capability allows it. Preserve Tracker text: article, part name, quantity and mechanic login. Map server codes to Russian copy without raw integration values.

- [ ] **Step 4: Verify integrated web domain**

Run: `cd apps/web && npm test -- --run src/domains/inventory src/app/park/ParkScopeProvider.test.tsx`

Expected: all inventory tests PASS for role/park matrices.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/inventory apps/web/src/app/park/ParkScopeProvider.tsx apps/web/src/app/park/ParkScopeProvider.test.tsx apps/web/src/i18n/ru.ts
git commit -m "feat: export scoped inventory"
```

### Task 10: Browser journeys and responsive layouts

**Files:**
- Modify: `apps/web/e2e/operational/fixtures.ts`
- Modify: `apps/web/e2e/operational/routeFixtures.ts`
- Create: `apps/web/e2e/operational/inventory-workflows.spec.ts`
- Modify: `apps/web/e2e/operational/route-role-layout.spec.ts`
- Modify: `apps/web/e2e/operational/responsive-visual.spec.ts`
- Modify: `apps/web/src/domains/inventory/inventory.css`

**Interfaces:**
- Produces deterministic mechanic/operator/admin/royal journeys at 320, 390, 768, 1024 and 1440 px.
- Preserves route readiness and only updates inspected visual baselines.

- [ ] **Step 1: Add failing E2E journeys**

```ts
test('mechanic completes the park stock cycle on phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openAs(page, 'mechanic', '/inventory?park=1')
  await page.getByRole('searchbox', { name: 'Найти запчасть' }).fill('ABC-1')
  await expect(page.getByText('Полка A-1')).toBeVisible()
  await postReceipt(page, 'ABC-1', 5)
  await postCount(page, 'ABC-1', 4)
  await expect(page.getByText('4 шт.')).toBeVisible()
  await expectNoHorizontalOverflow(page)
})
```

Add royal all-parks export, mechanic foreign-park denial, keyboard tabs, 44 px targets, one open large workflow and axe checks.

- [ ] **Step 2: Run failure**

Run: `cd apps/web && npx playwright test e2e/operational/inventory-workflows.spec.ts e2e/operational/route-role-layout.spec.ts`

Expected: fixtures and responsive CSS do not yet satisfy the journeys.

- [ ] **Step 3: Complete fixtures and CSS**

Mock catalog, stocks, receipts, counts and downloads with stateful per-test fixtures. Use cards below 600 px and list/detail grid above 899 px. Keep page overflow zero, search sticky only inside main, and avoid covering bottom navigation.

- [ ] **Step 4: Run browser/accessibility matrix**

Run: `cd apps/web && npx playwright test e2e/operational/inventory-workflows.spec.ts e2e/operational/route-role-layout.spec.ts e2e/operational/responsive-visual.spec.ts e2e/support/assertA11y.test.ts`

Expected: all permitted cases PASS; denied roles show standard access; inspected screenshots are readable.

- [ ] **Step 5: Commit**

```bash
git add apps/web/e2e/operational/fixtures.ts apps/web/e2e/operational/routeFixtures.ts apps/web/e2e/operational/inventory-workflows.spec.ts apps/web/e2e/operational/route-role-layout.spec.ts apps/web/e2e/operational/responsive-visual.spec.ts apps/web/src/domains/inventory/inventory.css
git commit -m "test: cover inventory workflows end to end"
```

### Task 11: Full verification, release 0.1.33 and live cycle

**Files:**
- Modify: `VERSION`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Modify: `apps/api/src/robopark_api/services/ops/context.py`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `deploy/release-metadata.json`
- Create at packaging time: `artifacts/robopark-0.1.33-${release_sha}/robopark-release-0.1.33.zip`

**Interfaces:**
- Produces version `0.1.33`, manifest migration head `0026_global_inventory_workflows`, signed artifact and verified live release.

- [ ] **Step 1: Run full local verification**

Run: `./scripts/verify.sh api`

Expected: Ruff, format and all API tests PASS.

Run: `./scripts/verify.sh web`

Expected: lint, production build, all Vitest tests and `check-nav` PASS.

Run: `cd apps/web && npx playwright test`

Expected: all applicable Chromium journeys PASS; only documented unsupported cases skip.

Run: `./scripts/verify.sh host`

Expected: updater, retention, signature, migration compatibility and rollback tests PASS.

- [ ] **Step 2: Set one canonical release version**

Set `0.1.33` in all shipped version sources and mechanic-first notes in metadata, then run:

```bash
apps/api/.venv/bin/python scripts/check-release-version.py
git diff --check
git add VERSION apps/api/pyproject.toml apps/api/uv.lock apps/api/src/robopark_api/services/ops/context.py apps/web/package.json apps/web/package-lock.json deploy/release-metadata.json
git commit -m "release: prepare global inventory OTA"
```

Expected: `0.1.33` and successful commit.

- [ ] **Step 3: Build and verify the signed artifact**

```bash
release_sha=$(git rev-parse --short=7 HEAD)
release_dir="artifacts/robopark-0.1.33-${release_sha}"
mkdir -p "$release_dir"
ROBOPARK_SIGNING_KEY_FILE=/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/armbian-installer-ota/.release-secrets/release-signing.pem ./scripts/pack-release.sh "$release_dir/robopark-release-0.1.33.zip"
python3 scripts/verify-artifact.py "$release_dir/robopark-release-0.1.33.zip" --public-key /Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/armbian-installer-ota/.release-secrets/release-public-key.pem
shasum -a 256 "$release_dir/robopark-release-0.1.33.zip"
```

Expected: detached Ed25519 signature, content, version, git SHA and migration head verify without printing key contents.

- [ ] **Step 4: Install and verify live role cycle**

Upload through `/api/admin/ops/update/inspect`, approve with `ОБНОВИТЬ`, wait for `state=succeeded`, then require readiness 200. Verify live mechanic search → receipt → stock → count → own export → task writeoff; admin/royal global edit and all-parks export; operator selected-park edit; mechanic foreign-park 403. Compare deployed asset hashes with local build.

- [ ] **Step 5: Review, push and report**

Request final code review over the complete branch. Resolve every Important or higher finding with a RED→GREEN regression and rerun affected suites. Push `main`, verify `git ls-remote origin refs/heads/main` equals HEAD, and report artifact path, SHA-256, test counts, OTA job ID, live checks and any stale health-publication caveat.
