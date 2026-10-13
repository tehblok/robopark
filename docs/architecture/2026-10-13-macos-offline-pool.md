# Mac offline workspace and shared Emergency pool

Status: implementation authorized by the user on 2026-10-13. The user explicitly
requested autonomous execution (up to seven hours), offline work, distribution
across connected Macs, and trust after service-account login without manual
device approval. This supersedes the first pilot's pending-device policy.

## Outcome and boundaries

One macOS installer contains the complete existing Robopark web workspace and
the native outbound agent. The workspace starts even when the remote visual
site is absent. After an initial online account login, authorized saved data
and supported queued work remain available offline. Existing server checks,
idempotency, conflict resolution and the 72-hour offline identity window remain.
No offline state grants new server permissions. Fresh telemetry requires an
online source; stale snapshots must show their age.

All active, approved service-account devices are trusted by default after
cryptographic enrollment. Account suspension, device revocation, role/VIN/park
scope still apply. Devices need actual Emergency access to serve that workload.
No automatic access to arbitrary computers merely because they share a LAN.

## Chosen design

- Reuse the existing React workspace and IndexedDB sync engine rather than
  duplicate domain workflows in Swift. Package its production assets in the
  app; expose them through a bounded native HTTP service bound to IPv4 loopback
  only. A dedicated WKWebView uses a stable local origin and a random per-run
  HttpOnly session cookie. Host/Origin/path/size checks protect the local API.
- Native code proxies only relative `/api/` requests to the signed deployment
  origin. Server session cookies remain in Keychain/native memory, never in
  browser JS. Login and logout manage that account session. Enrollment after
  login uses the existing single-use code flow internally; device identity
  remains distinct from the browser sync installation identity.
- Server change clears the local workspace scope. Protected Mac offline
  storage uses AES-GCM with a key supplied by the native Keychain service;
  the browser/PWA retains its existing scoped storage contract.
- Add an explicit bounded offline preparation flow for selected authorized
  data, with freshness/count/limit information, durable per-install sync ID,
  logout/revocation cleanup and reconnect refresh. Queue only operations the
  existing sync API supports; display unsupported online-only actions clearly.
- Broker uses observed latency, capacity and in-flight count with aging/fairness
  so eligible devices cannot starve. Apply bounded queue/backpressure, shared
  request deduplication, global endpoint rate limits and per-owner fairness.
  Slow/failing paths are excluded promptly; at most two attempts per request.
- Probe traffic is broker-budgeted rather than every Mac polling independently.
  Heartbeats carry capacity, pause/load/power state. Normal successful work
  refreshes readiness, reducing extra probes.
- Native agents compress bounded Emergency JSON using raw DEFLATE (RFC 1951)
  before returning it. Broker validates encoded and decoded bounds, then the
  existing schema, VIN and authority. Metrics expose completed jobs, attempts,
  queue/busy state and bytes to measure the actual gain.

Ordinary user writes stay on the central authenticated sync API. No shell jobs,
credential sharing, public Mac listeners, new Tuna ports or peer routing are
introduced. Direct peer file delivery and GPU/LLM services need separate real
workload contracts; this release distributes Emergency reads and performs
workspace rendering, offline reconciliation and media preparation locally.

## Acceptance

1. Cold offline workspace start with bundled assets and previously prepared
   scoped data, safe queue/reconnect, account switching and revoked access.
2. Login enrolls a Mac without admin approval, inactive/revoked owners rejected.
3. Deterministic and concurrent pool tests for 0/1/2/12/100 agents: participation,
   weighted load, disappearance, deadlines, bounded memory and probe rate.
4. Compressed/plain compatibility, decompression bombs and malformed frames
   rejected, metrics account bytes without payloads/secrets in logs.
5. Local HTTP negative tests: non-loopback hosts/origins, missing session,
   traversal, oversized/ambiguous HTTP, disallowed redirects and WS boundaries.
6. New local source commit, new version, Mac pkg and Linux OTA from that same
   clean commit; signatures, hashes and embedded web assets verified.

Physical Khadas/AGX and corporate SSO checks need the later supplied hardware
access. No remote deployment is authorized by this implementation request.

Sources: Apple Compression documents COMPRESSION_ZLIB as raw RFC 1951 DEFLATE:
https://developer.apple.com/documentation/Compression/COMPRESSION_ZLIB
