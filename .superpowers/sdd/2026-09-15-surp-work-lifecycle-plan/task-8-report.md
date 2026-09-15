# Task 8 report — Work as the operations home

## Delivered

- Made `/work` the canonical operations destination. `/overview`, `/dashboard`, `/operator`, `/tasks`, and `/admin/tracker` redirect there while preserving the selected park query.
- Removed the Startrek dashboard from navigation and arranged operator mobile tabs as Overview, Work, Robots, Campaigns.
- Rebranded the shell and guarded screenshot watermark to «СУРП», with the expanded accessible product name and a `tehblokdan` developer attribution.
- Simplified Work to a fixed oldest-first queued default without a manual status selector, while preserving a non-default status supplied by a deep link.
- Added a collapsed, responsive shift summary which does not request data until opened and then uses the existing cached `dashboardSummary` resource.
- Pinned a mechanic's independently queried open owned tasks above the park queue and removed duplicates from the queue.

## TDD evidence

- RED: the focused Task 8 suite initially reported 16 expected failures and 181 passes.
- GREEN: the focused suite then passed 236/236 tests across 8 files.
- A final regression test was added for a mechanic's queued default; its RED command was blocked after the package-manager fallback moved the local dependency tree to `node_modules/.ignored`. The one-line production correction was applied, but this final test could not be rerun without mutating dependencies again.

## Verification

- `git diff --check`: passed.
- `oxlint` over all changed TypeScript/TSX files, using the intact sibling tool installation: passed.
- Navigation source consistency, using the sibling TypeScript runtime against this worktree's sources: `check-nav: ok (30 route ids)`.
- Focused Vitest before the dependency relocation: 8 files, 236 tests passed.
- TypeScript/build rerun: blocked by the relocated local `node_modules`; the sibling binaries cannot resolve dependencies from this worktree's source tree without altering dependency resolution. No dependency files were modified further after that instruction.

## Files changed

- Routing manifest/router and tests.
- App shell branding, responsive navigation tests, and styles.
- Work page, filters, workbench, responsive styles, and tests.
- Russian product copy, HTML title, and screenshot watermark/test.
