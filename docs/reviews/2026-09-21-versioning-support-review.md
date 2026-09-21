# Review: versioning and support lifecycle

Date: 2026-09-21  
Implementation SHA: `ff92152`  
Range: `e424a1a..ff92152`

## Verdict

Independent read-only review: **PASS** after correction of all reported P1/P2
findings. No remaining P0–P2 finding was reported.

Corrected during review:

- old installations now fail closed with `bridge_required` before mutation;
- stable promotion requires the exact full/platform/load/soak gate set;
- installed release/support state is published by the host only after durable
  success and cannot retain a failed candidate after rollback.

## Short verification completed

- `./scripts/verify.sh fast`: 59 passed;
- TypeScript project build: PASS;
- focused release/host/API contract set: 79 passed;
- focused SystemVersionPanel test: 1 passed;
- final affected updater/release set: 122 passed;
- changed-area static checks and `git diff --check`: PASS.

## Explicitly unverified

By user constraint, this review did not run full API/web suites, PostgreSQL or
Docker gates, target-host installation, upgrade/recovery on physical hardware,
200-user load, soak, OTA creation, signing or publication. Therefore
`0.2.0-rc.1` is a prepared source candidate, not a published stable release.
