# Temporary repair classification and guided fields

## Behavior

A mechanic takes a task with one action. If its component field is empty, the reliable queue resolves the active `ROBOT_UNSORTED` component by name and writes its ID before requesting the work transition. Existing real components are preserved. A missing or ambiguous temporary catalog entry prevents the transition and produces an actionable sync error.

The completion form replaces the temporary value with the chosen real part. Known catalog names have readable Russian labels and search synonyms. Component selection prioritizes relevant defect codes; component and defect selections prioritize performed-action buttons. All canonical choices remain accessible, and suggestions never assert a diagnosis or completed action. Broad search synonyms are deliberately excluded from automatic title matching.

The temporary write uses the version of the same fresh Tracker resource that was inspected. A concurrent edit causes retry and a fresh read, preserving a newly selected real component. Structured completion rejects temporary values even when their numeric ID remains only in the current issue after catalog removal. Older queued actions retain their original semantics.

Repair options are prefetched only after the claim is confirmed, so the local cache does not capture the component field before the temporary write. Existing scoped offline caching, operator review, photo requirements and conflict recovery are preserved.

## Evidence

- New regression tests were observed failing before implementation for single-action claim, synonym search, linked field suggestions and pending-claim cache timing.
- API focused workflow/outbox/offline suite: 78 passed. Guidance tests: 3 passed. Final standard API gate (`pytest -p no:cacheprovider -q -m 'not load'` from `apps/api`): 2,693 passed, 22 skipped, 1 load test deselected; 22 existing dependency/SQLite deprecation warnings (663.85 seconds).
- Final web suite: 2,765 passed across 191 files. TypeScript/Vite build and lint passed.
- Browser lifecycle and spacing suite: 273 passed, 8 skipped. The real local API bridge starts with an empty component field; both desktop and mobile scenarios assert the temporary ID after claim and the real component after report delivery. Coverage includes shift handoff, operator return/approval, duplicate prevention and mobile outage/reload recovery.
- Repair-form automated accessibility checks passed; desktop and mobile captures were visually inspected.
- OTA builder/verifier/repository governance: 49 passed. Navigation, contrast, migration heads, release documentation, technical-debt registry and module boundaries passed.
- Independent review identified an over-broad title synonym risk; it was fixed and re-reviewed. Final API review found no material blockers.

## Scope limits

No production site installation or eight-hour soak was performed. Browser evidence uses synthetic Tracker fixtures. The runtime resolves IDs from the current queue; fixture IDs are not production configuration. Private exports remain excluded from Git and OTA. No migration, OTA v1 format change, model inference or autonomous ticket closure is introduced.
