# Mac compute implementation plan

> Execution: subagent-driven-development; explicit autonomous authorization from the owner.

**Goal:** deliver working cross-Mac photo/CSV processing, reliable Mac app and rc.2 prerelease.
**Architecture:** separate typed compute scheduler in existing broker, bundled Swift worker, existing caller fallback.
**Stack:** FastAPI/Python, Swift/ImageIO/Foundation, React/TypeScript.
**Spec:** docs/architecture/2026-10-14-macos-compute.md

## Constraints and review focus

- Test origin/UUID unchanged; production and Latest stable untouched.
- No arbitrary commands/URLs; enforce all byte/time/queue limits from spec.
- Stale result, retry/disconnect, revocation and resource draining must be tested.
- No missing-worker/failed transform may discard an original attachment.
- All-device compute registration must not grant Emergency authorization.
- CSV quoting and formula protection must preserve current export contract.
- Packaged lifecycle and update restart behavior must be checked, not inferred from compilation.

## Work packages

- [ ] Server: failing protocol/scheduler tests; compute broker/client/router, auth, leases/limits, existing inventory CSV integration; regression tests.
- [ ] Mac: failing protocol/workload tests; bundled isolated compute worker, compute channel and app lifecycle/panel integration; real image/CSV worker tests and installer checks.
- [ ] Web/integration (root): API photo preparation with local fallback, actual upload entry points, compute graph/status; browser and mixed Swift/Python end-to-end tests.
- [ ] Review: independent server/Mac/cross-system review, fix findings, repeat affected checks.
- [ ] Release: bump rc.2, full checks/builds, clean source commit, signed pkg/catalog/OTA/Khadas installer, prerelease upload/hash and Latest verification.
- [ ] Update existing project wiki with evidence, remaining physical checks and release links.

Ownership: server worker owns API compute modules and inventory export; Mac worker
owns apps/macos and Mac packaging tests. Root owns web/nginx, integration harness,
release/docs. Coordinate contracts through this spec; do not revert each other.
