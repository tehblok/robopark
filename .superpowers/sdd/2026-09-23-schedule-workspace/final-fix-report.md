# Schedule workspace final fix report

## Outcome

- Replaced the schedule list's silent 2000-row cutoff with bounded keyset pagination ordered by `(start_at, id)`. The list body remains backward-compatible; continuation is explicit through `X-Schedule-Has-More`, `X-Schedule-Next-Start-At`, and `X-Schedule-Next-Id`.
- The web schedule client follows every continuation page for the same park/owner/window and aborts the whole pagination chain when the workspace scope changes.
- Period copy now reads the exact selected owners and half-open source range in deterministic `(start_at, id)` order, rejects a 5001st source row before inserting anything, and returns all rows up to the shared 5000-row bound.
- Copy requests sent by the planner carry a stable retry key. The API records/replays them with `ReliableAction`, including ambiguous-response retries without duplicate inserts. Callers that omit the new optional key keep the previous request shape.
- The team grid renders only approved participant rows returned by the participants endpoint; schedule entries with unknown owners are no longer synthesized as mechanics. Personal-calendar filtering remains unchanged.
- Schedule week/month route-owner drivers now target the current calendar controls and `rp-schedule-calendar__days--week|month` surfaces.

## TDD evidence

The new focused regressions were run before implementation and failed for the intended reasons: copy replay inserted duplicates, copy truncated/accepted over-limit input, schedule pagination headers were absent, the client stopped after one page, scope requests were not abortable, unknown owners appeared as mechanics, and the copy retry key was absent.

## Changed files

- API: `apps/api/src/robopark_api/{routers/schedules.py,schedule_schemas.py,services/schedules.py}`
- API regression coverage: `apps/api/tests/test_schedules.py`
- Web transport/workspace/planner/grid: `apps/web/src/api.ts`, `apps/web/src/domains/shift/{ScheduleWorkspace.tsx,SchedulePlanner.tsx,ScheduleTeamGrid.tsx}`
- Web/unit coverage: corresponding `*.test.ts(x)` files plus `apps/web/src/api.test.ts`
- Route evidence/driver coverage: `apps/web/src/app/routing/routeStateEvidence.ts`, `apps/web/e2e/operational/route-owner-contracts.spec.ts`

## Verification

- `apps/api/.venv/bin/pytest apps/api/tests/test_schedules.py -q` — **21 passed**.
- `npm test -- --run src/api.test.ts src/domains/shift/ScheduleWorkspace.test.tsx src/domains/shift/SchedulePlanner.test.tsx src/domains/shift/ScheduleTeamGrid.test.tsx src/app/routing/routeCoverageManifest.test.ts` — **52 passed**.
- `npx playwright test e2e/operational/route-owner-contracts.spec.ts --grep 'route-coverage:schedule:(week|month)|schedule week and month' --workers=1` — **3 passed**.
- `npx playwright test e2e/operational/schedule-workspace.spec.ts --workers=1` — **4 passed**.
- Focused Ruff and oxlint on changed files — **clean**.
- `git diff --check` — **clean**.

`npx tsc -b --pretty false` was also attempted and stopped on three pre-existing inference errors in `ScheduleWorkspace.test.tsx` (`kind: "sick"` against a narrow fixture type and two widened participant `role` arrays). None originates in the changed production contract; they were left untouched to keep this final fix isolated.
