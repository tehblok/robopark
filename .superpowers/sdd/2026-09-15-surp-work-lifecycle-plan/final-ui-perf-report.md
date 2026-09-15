# Final UI, performance and recovery fix report

## Result

- Work list SLA enrichment now uses one process-wide two-worker pool, a bounded
  admission gate, per-key single-flight, a 512-entry/60-second cache and a
  250 ms total list budget. A cold 200-item page starts at most two history
  reads and returns unresolved rows as explicit estimates. Detail keeps its
  exact history path.
- Admin/royal can idempotently retry every `needs_attention` action for one
  visible task. Existing action IDs, errors and attempt counts are retained;
  one separate completed control action and one audit row record the request.
- Work exposes manager-only hidden-task filtering, reason-required hide,
  hidden reason/restore, and retry-now feedback. Hidden detail does not start a
  forbidden timeline resource loop. Non-manager requests strip the hidden
  filter before calling the API.

## TDD and verification

- RED: the stalled 200-item history test exceeded 30 seconds; recovery tests
  failed on the missing helper/route; five web tests failed on the missing
  API/filter/control behavior.
- GREEN: API affected suite: `90 passed`; web focused suite: `121 passed`;
  TypeScript `--noEmit` passed.
- Ruff check passed and all changed Python files pass Ruff format check.

The bundled package-manager wrapper attempted a registry lookup and moved
installed packages into `node_modules/.ignored`; the moved directories were
restored without changing source manifests or lockfiles. Lint/build are run
directly with the existing local binaries to avoid network/package mutation.
