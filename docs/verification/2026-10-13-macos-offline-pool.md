# RC44: Mac offline workspace and Emergency pool verification

Release: `0.2.0-rc.44.dev7114536505405942334`.
Local source branch: `codex/agx-rc44-test`. No production deployment or publication.

## Verified behavior

- The package includes the production React workspace, native loopback HTTP
  service, outbound Emergency agent and per-user LaunchAgent. No extra Tuna
  ports or runtime Node/Python installation on the operator Mac.
- An approved account with Emergency permission enrolls its Mac automatically.
  A lost enrollment response can be recovered with the retained key and a fresh
  one-use code, without consuming another active device slot. Logout immediately
  stops local participation; failed Keychain deletion cannot resurrect it on
  restart. Pending server revocation is retried for the next authenticated owner.
- Offline preparation atomically publishes a bounded snapshot. Quota failure
  preserves the previous complete snapshot and does not claim readiness.
  Account/park scope, 72-hour identity expiry and server conflict/idempotency
  checks remain in force. A cold start restores pending chat messages from the
  durable command queue even when the timeline cache predates those messages.
- Desktop snapshots, identity, commands, media, share inbox and drafts use
  AES-GCM. Complete command/control records are sealed; exposed index fields
  are checked against authenticated contents. Temporary key unavailability
  fails closed and preserves recoverable ciphertext. Local OS administration
  is outside this protection boundary.
- Emergency scheduling reserves capacity before asynchronous authority checks,
  balances eligible devices using latency/capacity/load/aging, merges concurrent
  VIN reads, caches for three seconds and bounds queues/probes/upstream calls.
  Ineligible owners, revoked sources, malformed/compressed oversized responses,
  old generations and unavailable sources are rejected.

## Checks

The detailed build/check logs are copied into the ignored release directory
`artifacts/rc44-offline-pool/verification/`. The artifact report in that directory
records the final commit and SHA-256 hashes; it is generated after source freeze.

| Check | Result / scope |
| --- | --- |
| API full pytest | 3,104 passed; 37 skipped; 24 warnings, 1,009.09 s |
| Disposable PostgreSQL integration | 33 passed; 2 warnings, 16.46 s |
| Host/install/OTA/packaging pytest | 1,495 passed; 8 skipped, 101.37 s |
| Web full Vitest | 2,976 passed in 206 files, 34.07 s |
| Desktop browser scenarios | 24 passed: four cases × Chromium/WebKit × three repetitions, 52.7 s |
| Web release build | TypeScript + Vite + service-worker/static compression completed |
| Web lint/navigation/build-script checks | lint passed; 33 route IDs; 53 script tests passed |
| AgentCore Swift executable checks | 43 passed |
| Native loopback/proxy/compression checks | 36 passed |
| Production WorkspaceSession hermetic integration | 16 passed |
| Production Bridge lifecycle hermetic integration | 8 passed |
| Independent read-only review | No remaining reproducible P0/P1/P2 after the identified fixes |

The API full run skips optional PostgreSQL cases (run separately above) and
environment-specific checks; inspect the saved logs for the exact list. Existing
warnings include duplicate OpenAPI operation IDs and upstream deprecations.
Swift emits CommandLineTools linker search-path warnings but builds successfully.

The first parallel web run exposed a wall-clock race in the terminal identity
test. The test now revokes identity after observing mounted output, retaining
the same production behavior assertion. The full repeated run passed. The
first host snapshot check correctly detected a concurrent source edit; final
packaging checks passed after all source writers stopped.

### Real local transport simulation

`apps/api/tests/test_emergency_bridge_pool_transport.py` starts the broker and
100 real local WebSocket clients with synthetic P-256 identities, then sends
actual RPC requests. It does not mock the broker dispatch/transport.

`pool-transport-verification.json` records:

- 100 connected clients, 100 parallel successful responses and all 100 clients
  used once in that wave (minimum/maximum assignments: 1/1).
- A repeated cached VIN causes zero new wire jobs; concurrent identical reads
  cause one wire job.
- Ten paused clients are not selected; disconnecting ten leaves 90 connected.
- An invalid compressed result returns 503; managed readiness probes execute.

This is a fan-out test, so it deliberately sets the per-requester concurrency
limit to 128 and upstream rate to 64/s. Shipping defaults remain 16 concurrent
requests per requester and 8 actual upstream reads/s (burst 8), with at most
2 managed probes/s; their bounds are covered separately by broker unit tests.
The clients use one synthetic approved owner. This is neither 100 physical Macs
nor a measured corporate Emergency load test. No corporate payloads or secrets
are stored in the report.

### Native and browser boundaries

`AgentCoreChecks` and `DesktopChecks` execute Swift production components,
including a real local listener, parser limits, session/Origin/Host enforcement,
cookie isolation and raw-DEFLATE compatibility. `check_macos_workspace_session.py`
copies exact production session/AgentCore sources into a temporary Swift package
with in-memory Keychain/trust shims and a hermetic URLProtocol transport. It
exercises cookie persistence, encoded auth routes, logout, failed-delete
tombstones, delayed-response fencing, server changes and response-size bounds.
`check_macos_bridge_lifecycle.py` likewise executes production Bridge code with
hermetic transport: wake during claim, explicit disconnect cancellation, reuse
of the enrollment key after a lost reply and retry after Keychain save failure.

Playwright serves the actual React code at the fixed local origin and mocks the
native key endpoint and remote API. It checks persisted encrypted data and
receipts, cold UI reload during a remote outage, a comment entered through the
UI and durable acknowledgement after reconnect, missing-key failure and the
desktop terminal restriction. Repeated requests may carry the same idempotent
action ID during auth revalidation; a confirmed command is not replayed after
the next reload. Playwright WebKit is an engine check, not proof of the installed
WKWebView app or corporate SSO.

## Physical acceptance still required

The `.ota` uses the existing ARM64 Khadas VIM4/AGX Orin installation contract.
No SSH/hardware access was supplied, so boot, thermals, hardware-specific
dependencies, Wi-Fi throughput and rollback must be checked on the selected
test device. The other device can later use the same approved release.

Test two Macs first: one without corporate access and one with it; then both
available, one sleeping/disconnected, and all unavailable. Check operator and
admin pool visibility, owner suspension/revocation, offline reload and queued
write reconciliation. Expand to 12 and then up to 100 actual installations while
observing upstream rate, queue time, failed attempts and server network traffic.

Apple Developer ID/notarization are unavailable on the build host. The test app
has an ad-hoc signature; the installer is unsigned by Apple. Update metadata is
Ed25519-signed with the existing release root. A real install/update/Keychain
continuity cycle and corporate SSO require the test Mac. Code/addresses cannot
be hidden from an administrator controlling that Mac. HTTPS/WSS terminates at
the configured ingress; this release does not add end-to-end encryption through
that termination point.

Emergency reads are distributed and compressed; rendering, image preparation
and offline storage run locally. The central server still handles authenticated
writes and carries response traffic. GPU/OCR/arbitrary jobs and direct peer
file delivery are not implemented. Actual server Wi-Fi savings need measurement.
