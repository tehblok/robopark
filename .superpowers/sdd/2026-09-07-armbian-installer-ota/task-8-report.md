# Task 8 — GitHub discovery and explicit Royal approval

## Implemented contracts

- Root `check-update` now reads only the installed `updater.env` (bounded, regular, no-follow, private owner/mode checks; literal parsing, never sourcing shell). The existing timer invokes this CLI. Disabled or broken configuration exports a finite public state and never runs an update.
- Discovery requests the configured exact GitHub repository's releases list, filters strict bounded SemVer and stable/prerelease channels, excludes drafts, verifies exact tag/release/API-asset identities, and requires the four Task10 release assets. Independently published installer assets may coexist. Asset browser URLs and release-note HTML are ignored.
- Version, commit SHA, migration head, filename, archive size, SHA-256 metadata/checksum and optional GitHub asset digests must agree. Private history keeps the immutable release descriptor/high-water release ID per repository; lower IDs, changed descriptors and consumed release IDs cannot be approved again.
- The HTTP boundary uses explicit Accept/User-Agent/API-version headers, optional bearer token sent only to `api.github.com`, no environment proxies, 15-second socket timeouts, a 120-second client budget, `read1` streaming and bounded response sizes. API redirects are refused; at most three asset redirects can reach the exact HTTPS `release-assets.githubusercontent.com` / `objects.githubusercontent.com` hosts. Redirect requests never carry Authorization. Rate limits, network truncation and transport-policy refusals become sanitized `discovery_stale` state.
- Root writes `state/available-update.json` mode 0600 and an allowlisted `public/available-update.json` projection mode 0644. Neither contains tokens, headers, URLs/queries, raw API bodies, release notes, or tracebacks. Availability expires after 24 hours at both the API and root boundaries.
- Approved downloads re-fetch the exact immutable release ID and compare every discovered descriptor. They stream into a mode-0600 `.partial` in private `state/github-artifacts` (mode 0700), cap the byte count, fsync, validate Task10 checksum and raw 64-byte Ed25519 detached signature, and run the independent host internal-manifest/file-hash verifier before atomic publication.
- Task6 resolves reserved `github-release-ID.zip` artifacts from root-private storage, so API-writable uploads with the same name cannot replace them. The worker rechecks the exact root approval (job, actor, timestamp and artifact), raw detached signature, exact Task10 metadata and internal hashes immediately before apply. Public/manual uploads retain their existing artifact location.
- The Task7 root consumer accepts only a bounded `github-update` request containing the discovered release ID and actor, converts successful verified downloads to the existing Task6 update command, and serializes them under existing command/host locks. Interrupted downloads fail closed and remove partial files; an interrupted root claim never silently repeats approval. Discovery never installs anything.
- Royal-only `GET /admin/ops/available-update` and `POST /admin/ops/github-update/approve` are implemented. POST accepts only `{release_id, confirm}` and requires exact `ОБНОВИТЬ`. Repeated active approvals reuse the same job; a fresh explicit approval after a failed, unconsumed network download can retry. Admin/operator access returns 403.

## API contract for Task 9

GET returns:

```json
{"state":"available|up_to_date|discovery_stale|disabled|approved","checked_at":"ISO timestamp or null","release":{"release_id":101,"version":"1.3.0-rc.1","git_sha":"40 hex","size":1000,"sha256":"64 lowercase hex"}}
```

`release` is null unless state is `available`. POST returns the existing `OpsJobOut`.

Approval errors: `400 confirm_required`, `400 github_release_unavailable` (including stale/unknown/disabled), `400 github_approval_actor_mismatch`, `409 host_work_in_progress`, `409 job_in_progress`, `503 host_bridge_unavailable`, or standard 422 input validation. Host execution failures become an existing failed OpsJobOut with `host_operation_failed`; private host details are not exposed.

## SemVer compatibility

The parent explicitly approved extending Task6 and Task10 validators so prerelease discovery can actually install. Host precedence now handles stable/prerelease ordering, numeric prerelease identifiers, leading-zero rejection and bounded lengths; build metadata has no ordering weight. Task10 standalone verification accepts the same manifest versions. Its source/tag checker accepts canonical prerelease versions. GitHub names reject `+` build metadata to keep exact tag/asset URL identities unambiguous. Manual host artifacts can use build metadata; journal/retention basename validation was expanded for `+` and is covered through actual updater reconciliation. The legacy signed `min_installer_version: "0"` sentinel remains compatible and maps only there to `0.0.0`.

## TDD evidence

- Initial discovery suite: 36 RED failures because the implementation module did not exist.
- Host CLI/command and Royal route tests: expected RED failures for missing behavior/endpoints, then GREEN.
- Real Task10 prerelease packaging failed its original standalone numeric-only verifier, then passed after the aligned SemVer change. The same test packages, verifies, discovers and host-verifies the actual signed artifact.
- Targeted RED regressions caught public-artifact substitution, missing root approval/detached re-verification, stale partial files after an interrupted claim, network truncation/slow-stream bounds, failed-download retry, optional GitHub digest mismatch, nonroot state writes and transport refusal incorrectly appearing up-to-date.
- A full updater/reconciliation test caught build-metadata journals remaining in maintenance; the bounded basename fix passes for both prerelease and build-metadata versions.

## Verification

The worktree owns its own `apps/api/.venv`, installed by `uv sync --frozen --extra dev` from the available cache. No tests contacted real GitHub and no production host was changed.

- `PYTHONPATH=deploy/host:apps/api/src apps/api/.venv/bin/python -m pytest tests/host -q`: **396 passed**. Includes Task8 discovery/download, Task6 updater/recovery, Task7 host commands, and Task10 verifier/packaging regressions.
- API selection, cwd `apps/api`: **131 passed**, one existing Starlette/httpx deprecation warning. Files: `test_ops_host_bridge.py`, `test_ops_host_review.py`, `test_ops_http.py`, `test_security_hardening.py`, `test_ops_jobs_abort.py`, `test_ops_runner.py`, `test_ops_snapshot.py`, `test_ops_archives.py`, `test_release_signing.py`.
- Ruff check and format check using `--config apps/api/pyproject.toml` on all 13 changed/new Python files: **passed**.
- `git diff --check`: **passed**.

## Operational boundaries

Verification ran on macOS with temporary host roots, real signatures/archives/filesystem operations and fake external HTTP/Docker/systemd adapters. Live GitHub authentication/CDN redirects, actual Linux timer activation and Armbian installation remain deployment acceptance checks. Discovery examines at most the newest 100 releases and fails closed on unavailable metadata; it does not scan arbitrary URLs or additional pages. Downloads consume a release ID only after all signatures/hashes pass; a crash after durable consumption but before command publication intentionally requires a new release ID rather than replaying an ambiguous approval. Artifact/receipt retention is unchanged from the broader host operations lifecycle.

## Review fix round 1

Closed both Important findings and the private-state robustness note from `task-8-review.md`.

- The Task10 publisher now derives `--prerelease` from the already validated SemVer version, verifies the tag still equals `v$version` at publication, and leaves stable publication without the prerelease flag. Tests execute the actual workflow shell with a local fake `gh`, validate stable/rc source-and-tag consistency, reject mismatched tags and assert the resulting publication flags. The original real pack → standalone verify → discovery → host verification test now derives the GitHub `prerelease` field from these actual workflow flags instead of setting it manually.
- GitHub transport runs in a short-lived POSIX fork worker, supervised by a nonblocking byte pipe and parent-owned monotonic deadline. The parent enforces the remaining shared 120-second client budget (30 seconds for an individual API request), including time spent resolving, opening HTTPS, following redirects, parsing headers, and reading chunk framing. The parent checks time after each pipe read and worker completion; the worker also rejects late open/read/EOF completion. On timeout or generator closure, the parent terminates and joins the worker for at most 100 ms, then uses kill plus a further bounded 100 ms join if needed. No unbounded worker join or pipe-frame receive can hold the host lock. Socket timeouts remain capped by the remaining budget as an additional limit. Credentials remain in process memory; no token is passed on argv, persisted, logged or sent through the pipe.
- Private history now requires the exact state-object shape, a boolean consumed flag and a validated release/asset descriptor (including repository, ID and type bounds). Malformed JSON objects/values are normalized to `discovery_stale`, clearing the prior public release. The private availability envelope uses the same release validator before approval.

RED evidence: the publisher prerelease and tag-mismatch tests failed; null/list/scalar/invalid history shapes either escaped with AttributeError or kept misleading availability; a late EOF was incorrectly accepted; and all five blocking transport stages exceeded their short test deadline. These cases passed after the respective fixes. Additional tests exercise real `http.client.HTTPResponse` header/chunk `readline` paths with drip-fed input, shared budgets across requests, TERM-ignoring workers requiring KILL, expired ZIP partial cleanup, worker reaping and host-lock release.

Round-1 validation uses the same Task8-owned Python 3.13 environment:

- `tests/host/test_github_review.py`: **24 passed** (new review regressions).
- Full `tests/host`: **420 passed**, including Task8, Task10 packaging/verifier and Task6/7 host regressions.
- The same nine-file API/security selection listed above: **131 passed**, one existing Starlette/httpx deprecation warning.
- Ruff check/format check on the three changed/new Python files: **passed**.
- `git diff --check`: **passed**.

The HTTP worker relies on POSIX fork, matching the Linux host target; its deadline/termination behavior was exercised on macOS with safe fake transports and real child processes. No test contacted GitHub or published a release. Live GitHub/CDN and Linux/Armbian acceptance boundaries remain unchanged.
