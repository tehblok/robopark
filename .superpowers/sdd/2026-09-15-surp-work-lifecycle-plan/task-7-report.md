# Task 7 report: Unified work chat and action forms

## Result

The Task 5 workflow DTO now selects the simplified Work UI: one chronological timeline with
per-message attachments and plain-language synchronization state, a local comment composer,
closed parts and handoff disclosures, and role-derived review actions. Legacy task rendering is
kept only for pre-workflow fixtures/consumers and cannot expose manual transitions or a separate
Tracker button for real workflow responses; the ticket key remains the single Tracker link.

The review form loads the server defect catalog, accepts one searchable exact code, owns one
replaceable photo state and omits blank optional comments. At the 900px task breakpoint phones
get separate camera and file inputs; only the explicit camera input has `capture="environment"`.
Desktop gets file selection. Both paths share the same preview/remove/replace state.

## TDD and verification

- RED: three missing component imports, two absent lifecycle controls and the old history/manual
  action layout; the focused run reported five failed files with the expected causes.
- GREEN: focused Task 7 suite passed `5 files, 115 tests`.
- Fresh extended affected run passed `7 files, 136 tests`, including collaboration and legacy task
  card compatibility.
- TypeScript build, scoped oxlint and `git diff --check` completed with exit code 0 and no output.
- The repository `npm` command was unavailable in this worker PATH, so the checked-in packages
  were invoked with the configured bundled Node executable.

## Changed files

- `apps/web/src/api.ts`
- `apps/web/src/domains/work/TaskTimeline.tsx` and test
- `apps/web/src/domains/work/TaskSyncStatus.tsx` and test
- `apps/web/src/domains/work/SubmitReviewForm.tsx` and test
- `apps/web/src/domains/work/IssueWorkbench.tsx` and test
- `apps/web/src/components/tracker/IssueActionsPanel.tsx` and test
- `apps/web/src/components/tracker/TaskCollaboration.tsx`
- `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
