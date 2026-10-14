# Automatic hybrid compute routes · test RC3

The owner requests automatic direct WebRTC where available, server relay when direct is unavailable or breaks, recovery on later jobs, and minimum server Wi-Fi traffic for mechanic PWA plus operator/admin/royal Mac clients. Existing autonomous test-only delivery authorization applies; production stays untouched.

## Design
Use the existing orchestrator and typed Mac workers with on-demand P2P. PWA/browser clients submit work while installed Macs execute it. This is options 1+2; DHT/full mesh is future design, not required for automatic routing. Ordinary account, database and authoritative writes remain server operations. No additional public ports, arbitrary code or background PWA workers.

Photo preparation remains local-first to avoid unnecessary transfer; resource-poor clients may try direct first, but must try local processing before relay. Once remote work is necessary: direct attempt has a bounded deadline; failed/closed channel is cancelled and released before one HTTP relay upload. Server dispatch may retry a different Mac within its original deadline. Read/compute operations may repeat; only validated results reach the final existing idempotent mutation. Interrupted transfers restart bounded jobs, not byte-offset migration.

Shared web code runs in PWA and the bundled Mac workspace. Account changes and offline events cancel both routes and prohibit stale results. Authentication denial never triggers a fallback. Direct failures use exponential cooldown (10–60sec) and a later on-demand probe; network recovery resets it. No background connectivity polling. Server Retry-After suppresses repeated admission requests. Simultaneous work is bounded.

Before uploading image bytes, POST /compute/relay/admit with operation, exact UTF8 JSON payload length and SHA256. Approved active requester receives an owner-scoped, short-lived, single-use ticket. POST /compute/image requires X-Robopark-Relay-Ticket; the server claims it before reading the body, streams only the declared bounded bytes, verifies digest/typed schema, and dispatches. DELETE /compute/relay/{ticket} releases unused admission. No ready Mac or budget returns a small error and a retry hint. Budget/slot ownership lives in the central broker, not separate API processes: 2 total relays, 1 per requester, conservative 64MiB/min estimated traffic budget including retries. Accounting is documented as payload/estimate, not a physical network meter.

One shared validation path verifies result digest, actual JPEG bytes, bounded decode and dimensions for either transport. Retain source on any final failure; offline uploads follow existing queue. Shared API boundary also prepares task photo and online repair review images so share targets and all roles use the same routing. Explicit QR/documents/logs remain original.

## Evidence required
Fail direct before/after input, relay success, next direct recovery, revoked account, logout during phases, offline, deadline, budget denied/no workers (no payload upload), consumed/foreign/expired tickets, duplicate/concurrent submission, truncated/hash-invalid data, Mac disconnect and retry. Real Swift worker regression and real WKWebView direct check remain required; no claims about field NAT topology without physical devices. Test release only, signed metadata and artifact consistency, wiki readback.
