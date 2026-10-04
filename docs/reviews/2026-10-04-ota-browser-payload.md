# OTA browser development payload

The regular OTA selected every tracked apps/web file, including Playwright
sources and PNG baselines. The clean-install workspace builder already omitted
these folders. The regular builder now excludes the directory parts `e2e` and
`e2e-production` as well; no runtime, model, knowledge or migration path changes.

Three source-filter regressions failed before the change. Archive assertions
now verify neither folder exists in the actual ZIP. Runtime/source and knowledge
paths remain included. Builder, verifier, API/host parity and clean/workspace
install/update checks passed: 196 tests. An initial parity invocation used the
main checkout's editable API rather than this worktree; rerunning with this
worktree's API source on PYTHONPATH passed.

Independent consumer review found no installer/updater or production-build
dependency on these folders. Developer browser-test/demo commands require a
full Git checkout; the runbook documents this. Acceptance digest behavior is
unchanged and separately tested: changes to either browser suite still alter
the source fingerprint. Build from the extracted OTA remains an integration
check before publication.

Acceptance-scope and evidence tests passed: 17/17. No digest exclusion was added for browser test sources.
