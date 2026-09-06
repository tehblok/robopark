# Open-blocker status selection on Work

The Work screen now shows a single status selector, applied immediately. The
default status is Queued (New for drivers, whose permitted statuses exclude Queued).
An explicit `status=all` preserves a manual all-open selection across reloads and
keeps links to all tasks of a robot unfiltered. The queue comes from the selected park; a legacy URL queue cannot override it.
Technical park integration settings remain in administration. Linked robot,
assignee, age and untagged restrictions remain visible and can be cleared.
Changing status resets pagination. Completed-status URLs return to the first
page of open blockers. Work requests always use `open_only=true` and oldest-first
ordering; the general Tracker API retains its previous default for history views.

Task age is shown separately in a themed badge. Narrow entity containers place
content above status and action, including a narrow desktop master pane. The
responsive suite checks that age and status do not overlap.

## Live defect discovered during verification

Selecting waiting parts returned no tasks although the all-open list contained
three. Tracker QL is case-sensitive for the inspected workflow keys and labels:
a count-only probe on open blockers in park Next returned 0 for
`delieverywaiting`, 3 for `delieveryWaiting`, 0 for `ожидание поставки`, and 3 for
`Ожидание поставки`. STATUS_BUCKETS had lowercased both keys and labels.

Preserve original spelling/case in query aliases; normalize only for local
comparison. Regression tests assert the emitted waiting-parts, waiting-team,
queued and diagnostics clauses. Browser verification after the fix showed three
waiting-parts tasks, oldest first (57, 24 and 17 days).

## Validation notes

Run the full API suite from `apps/api`; migration tests resolve `alembic` relative
to that directory. Running it from the worktree root incorrectly fails migration
checks. API tests use isolated data and cache directories. No live mutations or
broad closed-history probes were performed for this change.

Final checks: 1061 API tests, 1500 web unit tests and 194 Chromium tests passed.
Production build, navigation check and theme contrast check passed. Lint completed
without errors (existing warnings remain). Code review found no blockers.

For the subsequent queued-default adjustment: 1500 web tests and build passed.
Chromium passed 192/194 on the full run; the two remaining expectations used the
old URL without `status=all`. After updating those expectations, all 5 workspace
navigation tests passed. The runtime code did not change between those runs.
Live `/work?park=1` selected Queued and loaded four matching tasks.
