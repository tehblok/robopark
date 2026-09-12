# SDD ledger — plan: docs/superpowers/plans/2026-09-11-global-inventory-workflows.md

## Setup

- Isolated worktree: `/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/global-inventory-workflows`, branch `codex/global-inventory-workflows`, base `bf96c31`.
- Baseline: API 1333 passed with one existing Starlette warning; web 122 files / 1812 tests passed, lint/build/check-nav passed with existing lint and chunk-size warnings.
- Spec authority: `docs/superpowers/specs/2026-09-11-global-inventory-workflows-design.md`.

## Pre-flight scan

| Scope | Produces / consumes | Finding / ruling |
|---|---|---|
| Task 1 | Creates catalog/document/stock persistence and migration | Internally consistent. Ruling: preserve legacy tables and nullable legacy movement link during this release so rollback and old task writeoff remain possible — cost if wrong: the schema carries temporary duplicate storage until a later cleanup release. |
| Task 2 | Consumes Task 1; creates access/catalog/stock services and compatibility adapters | Plan permission names need split semantics. Ruling: mechanic creation of a missing global part/component is authorized by park stock-manage access; only edit/archive/merge of existing global catalog data requires `inventory.catalog.manage` — cost if wrong: mechanics may create duplicates that admin/royal must merge. |
| Task 3 | Consumes Task 2 stock writer; creates receipt lifecycle | Internally consistent; duplicate lines normalize before posting and source uniqueness makes retries idempotent. |
| Task 4 | Consumes Task 2 stock writer; creates count lifecycle | Internally consistent; sorted row locks and preflight conflict collection precede all mutations. |
| Task 5 | Consumes Tasks 1–4; creates export service and dependency | Ruling: update the lock through `uv add openpyxl==3.1.5`/`uv lock`, never hand-edit only one dependency source — cost if wrong: frozen install fails and blocks release. |
| Task 6 | Consumes backend contracts; creates URL-tab shell and web types | Internally consistent; invalid `view` falls back to `parts` without dropping `park`. |
| Task 7 | Consumes Task 6; creates parts/manage views | Internally consistent; generation guard prevents stale search and responsive disclosure enforces one large editor. |
| Task 8 | Consumes Tasks 3/4/6/7; creates receipt/count views | Internally consistent; only unsent drafts live locally and server document status remains authoritative. |
| Task 9 | Consumes Tasks 2/5/6/7; creates export UI and task compatibility | Ruling: preserve existing operator all-active-park scope from prior product decision while mechanic stays assigned-park-only — cost if wrong: operator visibility is broader than a future least-privilege policy may prefer. |
| Task 10 | Consumes all UI workflows; creates browser fixtures and responsive coverage | Internally consistent; snapshots change only after inspecting actual images. |
| Task 11 | Consumes all tasks; creates 0.1.33 release and live verification | Internally consistent; version commit must precede packaging so manifest SHA is current. |
| Tasks 1 ↔ 11 | Both modify release metadata | Compatible: Task 1 changes migration head; Task 11 retains it while changing notes/version sources. |
| Tasks 2 ↔ 3/4 | Stock writer consumed by both document services | Compatible: one transactional writer and distinct `source_kind` values prevent duplicated balance logic. |
| Tasks 2/3/4 ↔ 5 | Export reads all domain models | Compatible: export is read-only and follows the same access service. |
| Tasks 2/3/4 ↔ 6 | Backend schemas feed web types | Ruling: web uses exact serialized field names from committed API tests, not speculative plan examples — cost if wrong: a small type/fixture alignment commit may be required. |
| Tasks 6 ↔ 7/8/9 | InventoryPage/API/CSS are shared composition points | Ruling: execute sequentially and stage exact owned paths; later tasks preserve earlier tests and contracts — cost if wrong: focused integration repair before Task 10. |
| Tasks 7 ↔ 9 | Parts view and task picker consume catalog/park-stock identity | Compatible: both use `catalog_part_id`, never resurrect park-local identity. |
| Tasks 8 ↔ 10 | Document UI feeds stateful browser fixtures | Compatible: Task 10 models committed HTTP contracts rather than component internals. |
| Tasks 5 ↔ 11 | Both modify API dependency/version files | Compatible: Task 11 changes only package version while preserving the openpyxl lock. |
| Tasks 7/8/9 ↔ 10 | Shared responsive CSS and fixture behavior | Ruling: Task 10 may make narrow CSS/test-fixture corrections but cannot redesign domain behavior — cost if wrong: a UI defect may require routing back to the owning task. |

## Tasks

Task 1: Ruling: allow `apps/api/tests/test_models_migration.py` into Task 1 follow-up — a schema migration owns the repository's exact-table/head/metadata assertions and leaving the known suite red would make the deliverable unreviewable — cost if wrong: Task 1's test scope expands beyond the original file list by one existing migration test file.
Task 1: fix round 1/5 (4 addressed, 1 new Important open — downgrade catalog-only movements, balance-chain rebasing, source idempotency, and location conflict fixed; same-park downgrade overcount found; commits 3b38b7f..826f706).
Task 1: fix round 2/5 (1 addressed, 0 open — downgrade canonicalizes aggregate stock and archives zeroed duplicates without deleting history; commits 826f706..2a2b7a1).
Task 1: minor (deferred): round-trip test proves one active duplicate and retained movement count but does not assert canonical ID or every movement field; final review should triage whether this precision matters.
Task 1: complete (commits bf96c31..2a2b7a1, review clean; one deferred minor).
Task 2: Ruling: replace nonexistent `tests/test_actor_lifecycle.py` with the real `tests/test_tracker_claims.py` plus inventory task-writeoff tests — these are the actual lifecycle/ownership contracts in this repository — cost if wrong: a differently named lifecycle suite outside the discovered tree could remain unrun until the full API gate.
Task 2: Ruling: allow `services/rbac_seed.py`, its focused test, and `tests/test_release_signing.py` into the pre-review fix — existing installed roles otherwise never receive newly introduced inventory capabilities and the full suite retains a stale migration-head assertion — cost if wrong: the generic seed path changes to grant newly created default permissions while still preserving deliberate revocation of previously existing permissions.
Task 2: Ruling: update the paired release-signing `from_heads` expectation to exactly `["0025_local_task_claims"]` together with head `0026_global_inventory_workflows` — the test must assert the committed release metadata as one compatibility contract — cost if wrong: older direct migration heads intentionally stop being accepted for this release.
Task 2: fix round 1/5 (7 Important open — deterministic park targeting, SQLite-atomic stock updates, transactional legacy creation, archive/restore integrity, catalog merge API, empty-component compatibility, and inventory audit coverage; review range 2a2b7a1..d912185).
Task 2: fix round 1/5 result (7 addressed, 5 new Important open — migrated legacy/global ID collision, merge duplicate document-source history, transactional mixed PATCH, concurrent catalog creation conflict mapping, and PostgreSQL first-stock race; commits d912185..495b3ba).
Task 2: fix round 2/5 started for the 5 open findings above.
Task 2: fix round 2/5 result (5 addressed, 3 new Important open — legacy path identity remains ambiguous, merge mutates immutable source history, update/restore uniqueness races return 500; commits 495b3ba..e96fcba).
Task 2: Ruling: merged historical movements remain attached to the archived source catalog row and retain every original structured field; current stock consolidates into the target and history readers follow merge aliases — this preserves immutable event/source identity while still presenting one active catalog item — cost if wrong: history queries must include aliases and direct physical movement rows will reference archived source IDs.
Task 2: fix round 3/5 started for the 3 open findings above.
Task 2: fix round 3/5 result (3 addressed, 4 new Important open — cross-park legacy adapter mismatch, reused-article routing ambiguity, missing persistent merge alias, and post-merge writes to archived source; commits e96fcba..9de51b1).
Task 2: Ruling: legacy overview will use a disjoint adapter-ID namespace for global catalog rows instead of attempting to reuse positive legacy database IDs — this is the only way to keep ID-only old routes safe across parks and reused articles — cost if wrong: any client that artificially rejects the API's integer ID because it is negative needs a small client compatibility fix; repository web paths accept integer strings.
Task 2: Ruling: merge needs a persistent source→target alias independent of stock quantity and movement history; history remains immutable and all future source references resolve to the active target — cost if wrong: migration/model surface expands slightly before release, but prevents hidden stock and lost history.
Task 2: fix round 4/5 started with a fresh implementer for the 4 open findings above.
Task 2: fix round 4/5 result (4 addressed, 2 new Important and 1 Minor open — source idempotency across merge aliases, downgrade balance duplication after merge without target stock, and canonical target ID in stock audit; commits 9de51b1..faa840b).
Task 2: Ruling: source idempotency is checked across the entire canonical alias family before inserting a movement — immutable historical rows stay untouched while replay cannot double-apply after merge — cost if wrong: each sourced write performs an extra alias-aware lookup.
Task 2: fix round 5/5 started with a fresh implementer for all 3 open findings above.
Task 2: fix round 5/5 result (2 Important and 1 Minor addressed, 0 open — alias-family idempotency, downgrade totals, and canonical audit fixed; commits faa840b..0f0d49f).
Task 2: Ruling: when downgrade must materialize a catalog-only item whose article collides with an archived legacy row, use a collision-free synthetic legacy article while preserving the original global data in the available fields — the old schema enforces unconditional article uniqueness and cannot represent both identities verbatim — cost if wrong: a rare rollback export shows a synthetic article for the newer item until re-upgrade.
Task 2: minor from Task 1 round-trip assertion remains deferred to final review; Task 2 itself complete (commits 2a2b7a1..0f0d49f, final review clean; live PostgreSQL unavailable, dialect SQL and locking strategy verified).
Task 3: fix round 1/5 (2 Important and 2 Minor open — receipt search, canonical PostgreSQL lock ordering after aliases, service-level reversal reason, and duplicate-line note preservation; review range 0f0d49f..de25b75).
Task 3: fix round 1/5 result (prior findings addressed, 2 new Important and 1 Minor open — PostgreSQL locale-independent Cyrillic search, global catalog lock scalability, multiline note deduplication; commits de25b75..e8b90e0).
Task 3: Ruling: PostgreSQL receipts take a shared transaction advisory lock for merge-alias stability while catalog merge takes the matching exclusive lock before row locks — unrelated receipt documents remain concurrent without an O(global catalog) scan — cost if wrong: PostgreSQL deployments must support standard transaction advisory locks, already part of supported PostgreSQL.
Task 3: fix round 2/5 started for all 3 open findings above.
Task 3: fix round 2/5 result (3 addressed, 1 new Important open — posted receipt reversal fails after catalog part archival; commits e8b90e0..1ce5e01).
Task 3: Ruling: only historical document correction may apply a reversing delta to an archived canonical part; new/manual movements remain forbidden and reversal must not reactivate or create an unrelated item — cost if wrong: archived stock can change solely to undo an immutable posted document.
Task 3: fix round 3/5 started for the archived-part reversal finding.
Task 3: fix round 3/5 result (1 Important addressed, 0 open — archived historical receipt can be reversed without enabling ordinary archived writes; commits 1ce5e01..bfaf000).
Task 3: complete (commits 0f0d49f..bfaf000, final review clean; live PostgreSQL contention unavailable, dialect SQL and lock-order contracts verified).
Task 4: fix round 1/5 (3 Important open — archived missing-zero-stock representation, arbitrary one-million actual limit, and cross-dialect Unicode casefold search; review range bfaf000..f309243).
Task 4: Ruling: persist an indexed Python-casefolded count name and backfill it in the unshipped 0026 migration so SQLite/PostgreSQL search semantics stay identical and paginated — cost if wrong: one derived column and migration backfill are added.
Task 4: fix round 1/5 result (3 addressed, 2 new Important open — unbounded Python integers exceed DB capacity and `%term%` search cannot use B-tree; commits f309243..a3c0adc).
Task 4: Ruling: new inventory quantities use signed BIGINT with explicit int64 validation/overflow checks — this supports massive legitimate stock while guaranteeing controlled errors — cost if wrong: values above 9.22e18 remain unsupported by the SQL schema.
Task 4: Ruling: count-document name search is normalized Unicode prefix search backed by range predicates and composite indexes; quick arbitrary substring lookup remains a parts-search concern — cost if wrong: users must type the beginning of an inventory-act name.
Task 4: fix round 2/5 started for both open findings.
Task 4: fix round 2/5 result (2 addressed, 4 new Important and 1 Minor open — legacy multipart int64 bypass, alias-group transient overflow, collation-dependent prefix range, PostgreSQL downgrade narrowing, and 32-bit stock version; commits a3c0adc..cbc9071).
Task 4: Ruling: store and index the Python-casefolded UTF-8 bytes as a binary search key; bytewise prefix ranges are identical across SQLite/PostgreSQL collations — cost if wrong: one additional derived binary column is maintained.
Task 4: Ruling: count lines resolving to one canonical part post one aggregated net delta with stable document+canonical source identity — this avoids transient overflow/underflow while retaining immutable count lines — cost if wrong: movement history is canonical aggregate rather than one movement per original alias line.
Task 4: Ruling: downgrade deliberately leaves legacy accumulator columns widened to BIGINT so values created by the new release survive rollback and the old application remains SQL-compatible — cost if wrong: downgraded schema types are wider than historical 0025 metadata but behavior is backward-compatible.
Task 4: fix round 3/5 started for all open findings.
Task 4: fix round 3/5 result (all prior findings addressed, 1 new Important open — task writeoff sends Tracker comment before local version-overflow validation; commits cbc9071..f2b928b).
Task 4: Ruling: task writeoff performs and flushes the local stock delta before the external bot comment, but commits only after the comment succeeds; validation failure sends nothing and Tracker failure rolls the local transaction back — cost if wrong: an extremely rare DB commit failure after a successful external comment still requires operational reconciliation without a full transactional outbox.
Task 4: fix round 4/5 started with a fresh implementer for the external-side-effect ordering finding.
Task 4: fix round 4/5 result (1 Important addressed, 0 load-bearing findings; commits f2b928b..2946240).
Task 4: minor (deferred): successful task writeoff commits stock/movement and then audit in a second best-effort transaction because existing `audit.record()` owns its commit; final review should decide whether a larger audit transaction refactor is warranted — cost if wrong: rare audit commit failure leaves a successful writeoff without its audit row.
Task 4: complete (commits bfaf000..2946240, review has one deferred Minor and explicitly accepted distributed commit-after-comment limitation).
Task 5: fix round 1/5 (3 Critical-equivalent P1 and 3 P2 open — inactive park history scope, Excel int64 precision, unbounded quadratic aliases, long comment truncation, in-memory pseudo-streaming, missing XLSX filters; review range 2946240..19f61fd).
Task 5: fix round 1/5 result (6 addressed, 2 P1 and 2 P2 open — false shared alias/export cap, COUNT/stream race, alias/history snapshot race, XML-invalid XLSX text; commits 19f61fd..7c22d5e).
Task 5: Ruling: capture only bounded primary-key snapshots under the shared alias lock, then stream payload rows by those IDs; exported rows and internal alias worksets have separate caps — cost if wrong: up to 100k integer IDs are retained per export, still a small deterministic memory bound.
Task 5: fix round 2/5 started for all 4 findings.
Task 5: fix round 2/5 result (4 addressed, 2 new P1 open — PostgreSQL multi-query READ COMMITTED inconsistency and payload fetch after snapshot permits draft-line disappearance; commits 7c22d5e..6c47444).
Task 5: Ruling: export uses a dedicated repeatable-read snapshot session and writes the final spooled file before ending that snapshot; network streaming never holds a DB session — cost if wrong: export does synchronous spool preparation before response headers, bounded by 100k rows and disk rollover.
Task 5: fix round 3/5 result (2 P1 addressed, 0 open — one repeatable-read snapshot covers identity and payload spool; commits 6c47444..7679451).
Task 5: complete (commits 2946240..7679451, final review clean).
Task 6: fix round 1/5 (3 Important addressed, 2 new Important and 2 Minor found — legacy int64 codec, KPI placement, typed conflict narrowing, real viewport deferred; commits d62074d..0e5860f).
Task 6: fix round 2/5 (2 Important and typed-conflict Minor addressed, 0 code findings; commits 0e5860f..6d06f72).
Task 6: minor (deferred to Task 10): validate 390 px layout in a real browser rather than jsdom geometry.
Task 6: complete (commits 7679451..6d06f72, final review clean with Task 10 viewport check deferred).
Task 7: fix round 1/5 (8 findings reviewed; 5 addressed, 5 remaining/new — duplicate recovery park race, component pagination cap, merge search generation, mobile action menu, bulk labels; commits 26dfff3..e40a71a).
Task 7: fix round 2/5 started for the 5 open findings.
Task 7: fix round 2/5 result (5 addressed, 3 new/remaining — component-create park race, 4000-component truncation, stale label snapshots; commits e40a71a..13aba0d).
Task 7: fix round 3/5 started for the 3 open findings.
Task 7: fix round 3/5 result (3 addressed, 1 new P2 — created component missing from cache; commits 13aba0d..21bd367).
Task 7: fix round 4/5 result (1 addressed, 0 open; commits 21bd367..07641d4).
Task 7: complete (commits 6d06f72..07641d4, final review clean).
Task 8: fix round 1/5 (10 findings addressed, 3 remaining/new — independent manage/post permissions, backward-compatible 409 without affected_lines, historical alias metadata unavailable; commits 97c0e5a..e51947b).
Task 8: fix round 2/5 started for the 3 open findings.
Task 8: fix round 2/5 result (3 addressed, 3 new — unchanged receipt post rewrites merged source, supplier-null post blocked, partial count save blocked; commits e51947b..0189bea).
Task 8: fix round 3/5 started for the 3 open findings.
Task 8: fix round 3/5 result (3 addressed, 1 remaining — order-sensitive semantic dirty comparison; commits 0189bea..b572338).
Task 8: fix round 4/5 result (edit/revert addressed, line-order case remains; commits b572338..27641bd).
Task 8: fix round 5/5 result (order-independent baseline addressed, 0 open; commits 27641bd..8d99230).
Task 8: complete (commits 07641d4..8d99230, breaker review clean).
Task 9: fix round 1/5 (3 P1 addressed, 1 claim-authority gap remained; commits f6bca8a..358bc1f).
Task 9: fix round 2/5 (authoritative claim park addressed, 0 open; commits 358bc1f..f072b8b).
Task 9: complete (commits 8d99230..f072b8b, final review clean).
Task 10: initial implementation committed (stateful inventory fixtures, role journeys, responsive/a11y coverage, and inspected visual baselines; commit 485c592).
Task 10: fix round 1/5 (3 findings addressed — receipt POST idempotency, park+catalog-part stock isolation without global catalog mutation, and live open-editor/dialog overflow/44 px/axe/one-editor checks at phone/tablet widths; commit a9019ff).
Task 10: complete (commits f072b8b..a9019ff, final reviewer PASS with 0 open findings).
Task 7: fix round 1/5 (4 P1 and 4 P2 open — views not integrated, catalog photo ID collision, fixed 200-row management, cross-park mutation race, incomplete duplicate recovery/components, unsafe archive action, int64 input validation; review range 6d06f72..26dfff3).
Task 6: fix round 1/5 (3 Important addressed, 2 Important and 2 Minor remain — legacy int64 loss, shared KPI placement, typed error narrowing, 390px verification; commits d62074d..0e5860f).
Task 6: Ruling: KPI/viewport layout is verified in real-browser Task 10; Task 6 keeps structural CSS and honest component assertions — cost if wrong: a layout regression can survive until the E2E gate.
Task 6: fix round 2/5 started for both Important findings and typed-error Minor.
Task 6: fix round 1/5 (3 Important and 2 Minor open — structured 409 loss, JS int64 precision loss, shell coupled to legacy overview, dangling ARIA controls, ineffective jsdom mobile assertion; review range 7679451..d62074d).
Task 5: fix round 3/5 started for the single-snapshot findings.
Task 5: PAUSED by user during fix round 2 after RED `3 failed/11 passed`; uncommitted work is intentionally preserved in exactly `services/inventory_exports.py` and `tests/test_inventory_exports.py` (332 insertions, 52 deletions). Implementer reported ID snapshot under shared alias lock, independent caps, batched payload-by-ID, and XML escaping implemented; focused GREEN had just started and must be rerun on resume. Resume agent `/root/inventory_task5_exports`, do not reset or redispatch completed work.
