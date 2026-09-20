# SDD ledger — plan: docs/superpowers/plans/2026-09-19-clean-platform-redesign.md

Spec: docs/plans/2026-09-19-clean-platform-redesign-design.md
Branch start: dd6befe
Execution: subagent-driven, sequential implementation and per-task review.

## Preflight

| Tasks | Shared file/interface | Finding |
| --- | --- | --- |
| 1↔2 | DATABASE_URL, Alembic, DB health | Task 2 consumes Task 1 PostgreSQL runtime; order required. |
| 1↔7 | permissions/workflow services | Task 7 may amend shared services only with regression tests from Task 1. |
| 2↔4 | host runtime/doctor/retention | Task 4 extends Task 2 host profile; no parallel edits. |
| 2↔8 | pack/install/backup | Task 8 packages Task 2 output; metadata changes only in Task 8. |
| 3↔6 | resource cache/controllers | Task 6 consumes stable cache APIs from Task 3. |
| 3↔7 | change feed/resource keys | Task 7 must reuse keys and invalidation, not add another store. |
| 3↔8 | cache/load metrics | Task 8 consumes metrics from Task 3. |
| 4↔8 | pressure/soak reports | Task 8 verifies the retention and leak contracts from Task 4. |
| 5↔6 | shells/presentation slots | Task 6 builds task/robot presentations on Task 5 slots. |
| 5↔7 | shells/layout/tokens | Task 7 reuses Task 5 foundations; global structural CSS is forbidden. |
| 6↔7 | shared workflow controllers | Task 7 may extend but not duplicate these owners. |
| 6↔8 | visual/workflow acceptance | Task 8 verifies Task 6 against the approved A composition. |
| 7↔8 | route-role coverage | Task 8 consumes the complete manifest and evidence. |

Task/spec scan: all eight tasks agree with Global Constraints; no task mandates duplicate state, broad deletion, Redis, mandatory hardware acceleration or SQLite production.

## Baseline verification

- Base: `dd6befe209274fff928744dee547d8137dc63edf`
- Web: 145 files / 2065 tests passed; production build and navigation check passed; lint completed with 21 pre-existing warnings.
- API: 1766 passed, 1 skipped; 21 warnings; completed in 331.67s.
- Worktree was clean before Task 1 dispatch.

## Deferred findings for Task 7

- Inventory catalog must purge rows, selections and prepared labels on 401/403 and reject late responses.
- Campaign edit currently exposes a `kind` change that the API ignores; either implement the contract or make the field immutable.
- Interface A related-task tabs (`open`/`closed`) leave the visible repair/check/chat tablist without a keyboard entry point.
Task 1: complete (commits dd6befe..ec85822, tests: ./scripts/verify.sh api-postgres → 5 passed, 1 warning in 3.77s)
Task 1 review round 1: complete (commit 687a678, tests: canonical verify contract 2 passed; api-postgres 5 passed; api 1766 passed, 6 skipped)
Task 1 review round 2: CLEAN (canonical all-gate order and failure propagation verified; no scoped regressions)
Task 2: complete (commits 687a678..e5bcc2a, tests: env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/robot-check-readings/deploy/host:/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/robot-check-readings/apps/api/src apps/api/.venv/bin/python -m pytest -p no:cacheprovider -q tests/host apps/api/tests/test_ops_snapshot.py apps/api/tests/test_ops_runner.py → 732 passed, 1 warning in 428.54s (0:07:08))
Task 2 review round 1: complete (commit fc93880; host 710 passed; API ops 29 passed, 1 warning; packaging/release/GitHub 199 passed; Compose/Ruff/diff gates passed)
Task 2 review round 2: complete (commit b53898e; host 714 passed plus systemd 24 passed; installer 55 passed; real PostgreSQL 17 6 passed; API ops 29 passed; packaging/release/GitHub 224 passed; Compose/Ruff/shell/diff gates passed)
Task 2 review round 3: complete (commit e84edc3; host 718 passed; installer 56 passed; updater 96 passed; packaging/systemd/Compose docs 156 passed; focused 8 passed; shell/compile/diff gates passed)
Task 2 review round 4: complete (commit a156675; host 732 passed; installer 57 passed; focused 54 passed; API ops/bridge/agent 100 passed; API packaging 48 passed; updater/runtime/packaging/systemd 280 passed; Compose/Ruff/shell/compile/diff gates passed)
Task 2 review round 5: complete (commit c789774; host 742 passed; installer 58 passed; focused host 160 passed; API ops/bridge/agent/packaging 142 passed; final ops-agent/packaging 45 passed; direct/runtime/updater/legacy-host real Compose paths passed; shell/compile/diff gates passed)
Ruling: Task 2 accepted after breaker round — all round-5 load-bearing findings received direct regression tests and green real-Compose/full-host evidence; an additional sixth reviewer loop would exceed the five-round breaker without adding a new acceptance contract.
Task 3 review round 4: CLEAN (production provider route/mode transition verified; AppRouter suite 37 passed)
Ruling: Task 4 accepted after breaker round — the final starvation finding is load-bearing and received an addressable DB-selected cleanup path plus a real 4097-protected-file regression; all Task 4 safety contracts now have direct evidence.
Task 5 review round 3: CLEAN (filled context/action slots verified at 320/390/412/899/1440; Chromium 25 passed)
Task 6 review round 4: CLEAN (repeated parts action focus/scroll verified; scoped Chromium passed)
Task 7 review round 1 evidence closure: 205-state exact manifest bijection; parallel evidence shards 16+15 passed; inventory action×role API matrix 12 passed; web 2109 passed; relevant API 398 passed; camera/file/history/parity 48 passed; build/nav/lint/diff gates passed.
Task 7 review round 1: complete role/route suite finished (1140 collected: 846 passed, 294 policy-skipped, 0 failed); restricted robot detail correctly accepts confirmed short number or canonical unconfirmed VIN.
Task 7 review round 1: complete (commit c38c1b5; evidence audit 10 passed; evidence shards 16+15 passed; route/role 846 passed + 294 policy-skipped; web 2109 passed; relevant API 398 passed; camera/file/history/parity 48 passed; build/nav/lint/diff gates passed).
Task 7 review round 2: complete (commit e1c4e86; stable DomainPresentation identity/drafts; concrete dual-mode evidence; operator inventory read-only migration; campaign retry/toggle and real A report/campaign compositions; web 2116 passed; API 1838 passed, 7 skipped; evidence 63 passed; route/role 846 passed + 294 policy-skipped; build passed).
Task 7 review round 3: complete (short Alembic head plus real PostgreSQL 17 upgrade; exact per-state owner contracts; unique action×role HTTP matrix including inventory documents/export; campaign async/create evidence; api-postgres 7 passed; API 1967 passed, 8 skipped; web 2301 passed; evidence 63 passed; route/role 846 passed + 294 policy-skipped; build passed).
Task 7 review round 4: complete (347 real domain-owner behavior cases; executable admin/royal hard-delete matrix 13 passed; exact HTTP action matrices 141 passed; evidence 65 passed; route/role 846 passed + 294 policy-skipped; API 1968 passed, 8 skipped; web 2129 passed; build passed).
Task 3: complete (commit 486a075; API 1773 passed, 7 skipped; web 2070 passed; build/SW/lint gates passed)
Task 3 review round 1: complete (late IDB/auth races, global quota, Tracker membership/inflight invalidation, shared stale bound/metadata metrics, Tracker ETag, timer cancellation and real 200-viewer coalescing covered; final SHA and full-suite counts in task report/handoff)
Task 3 review round 2: complete (per-key hydrate generations, bounded retired server flights, auth-generation Tracker validators, and real route/interface-mode zero-duplicate GET covered; API 1779 passed, 7 skipped; web 2079 passed; build/SW/lint/Ruff gates passed)
Task 3 review round 3: complete (production-faithful InterfaceModeProvider/account/lazy-CSS transition assertion; focused helper consumers 136 passed; web 2079 passed; build/lint gates passed; no production change)
Task 4 review round 1: complete (TOCTOU-safe descriptor cleanup, bounded retry/report scans, sanitized host projection on real mounts, real owner wiring, software-only JPEG fallback; focused 69 passed; host 752 passed; API 1789 passed, 7 skipped; web 2080 passed; lint/build/Ruff/diff gates passed)
Task 4 review round 2: complete (eligibility/cap-aware bounded selector and real `/data` API pressure coordinator; focused host 11/API 72 passed; host 753 passed; API 1790 passed, 7 skipped; web 2080 passed; lint/build/Ruff/diff gates passed)
Task 4 review round 3: complete (dirfd-safe streaming live-merge prune, locked conservative temp lifecycle, global cleanup yield budgets, explicit production `/data` owner paths; focused host 34/API 46 passed; host 753 passed; API 1796 passed, 7 skipped; web 2080 passed; lint/build/Ruff/diff gates passed)
Task 4 review round 4: complete (lexical no-follow owner roots, bounded confirmed-upload scanning with propagated deadline/scan/deletion budgets and partial reports; focused API 113 passed; host 753 passed; API 1800 passed, 7 skipped; web 2080 passed; build/Ruff/diff gates passed)
Task 4 review round 5: complete (addressable DB-selected confirmed-upload cleanup makes bounded progress beyond arbitrary protected prefixes without directory enumeration; focused API 114 passed; full API 1801 passed, 7 skipped; Ruff/diff gates passed)
Task 5: complete (commit 169753a; explicit Classic/A shells, neutral account bootstrap, stable live owners/cache; web 2088 passed; build/lint/nav; interface Playwright 33 passed)
Task 5 review round 1: complete (commit 99d2913; layout-neutral More portal and distinct mode-owned DOM/CSS structure; web 2089 passed; build/lint/nav; interface and open-menu Playwright 37 passed)
Task 5 review round 2: complete (commit 6b92c68; mobile populated context/action stack at 320/390/412 while 899/1440 retain context rail; web 2089 passed; build/lint/nav; interface Playwright 42 passed)
