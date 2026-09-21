# Task 9 final gate fixes

## Scope

- Bridge the asyncio lifespan stop signal into an in-flight Emergency keepalive
  thread and join that thread before the loop returns.
- Require the production PWA smoke test in the canonical tag-release workflow
  before signing, packaging, and publishing.
- Keep the short `fast` verification target unchanged and do not add soak/load
  work to the release workflow.
- Signature verification semantics were not changed.

## Changes

- `ed89a24 fix(lifecycle): interrupt and join keepalive worker`
  - `run_keepalive_loop` now has a single shutdown bridge that immediately sets
    the worker's `threading.Event` when the asyncio stop event is set.
  - The executor task is shielded from cancellation; cancellation first signals
    the worker and then joins it, avoiding a detached writer thread.
  - Added a regression using the real loop and a blocking worker running in the
    actual executor thread; it proves prompt stop observation and a completed
    join.
- `4e55bf4 ci(release): gate tags on production PWA smoke`
  - The tag workflow runs `npm run test:e2e:pwa:linux` after full verification
    and before signing/packaging.
  - Static workflow tests pin the command and ordering.
  - Updated the existing CI entrypoint assertion to include the already-present
    PWA command.

## TDD evidence

- Keepalive regression failed before implementation with a 0.3 s timeout while
  the worker remained blocked; passed after the stop bridge was implemented.
- Release workflow ordering test failed because `Production PWA smoke gate` was
  absent; passed after adding the pre-package gate.

## Focused verification

- `pytest` for two keepalive shutdown cases and two workflow/config contracts:
  `4 passed` in 0.12 s (one existing Starlette deprecation warning).
- `ruff check` on keepalive implementation and tests: passed.
- `sh -n scripts/verify.sh`: passed.
- YAML parsing for `release.yml` and `ci.yml`: passed.
- `git diff --check HEAD~2..HEAD`: passed.
- Native production PWA smoke (`npm run test:e2e:pwa`): production build passed;
  Chromium PWA journey `1 passed` in 2.2 s. The first sandboxed attempt could
  not bind loopback (`EPERM`); the permitted rerun passed unchanged.

No full suite, Docker workflow, load/soak, installer, OTA, packaging, signing,
or publishing command was run.
