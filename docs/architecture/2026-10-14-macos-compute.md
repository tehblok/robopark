# Distributed Mac processing · test 0.2.3-rc.2

The owner explicitly requested autonomous implementation, verification and a new
test prerelease, with production untouched. This extends rc.1 beyond Emergency
reads: an authenticated Mac can execute bounded processing submitted by another
device or the server. Design/implementation decisions are delegated to this task.

## Priority update from owner: server Wi-Fi

The owner subsequently made server Wi-Fi bandwidth the primary constraint.
Photos are therefore processed locally first. If local processing is unavailable,
fails or the device is resource-constrained, use direct WebRTC DataChannel to a
free Mac. Never silently send the original to the server merely to find a peer.
The byte-relay endpoint remains an explicit bounded compatibility path, not the
automatic photo default. Store measured local reduction/direct bytes/signaling
bytes/fallback counts so UI does not equate CPU offload with network savings.

Direct protocol (normative addition): requester creates RTC offer with gathered
ICE (max3sec), then POST `/compute/direct` with operation=image_resize_v1,
payload_sha256, payload_bytes and offer={type:offer,sdp}. Request <=80KiB,
SDP<=64KiB, payload_bytes<=6MiB. Broker selects a compute channel advertising
`webrtc_datachannel_v1`, reserves its normal job slot and sends
`compute_peer_offer` containing generation/job_id/attempt_id/operation/digest/
payload_bytes/timeout_ms/offer plus a random 32-byte bearer `token`.
Mac returns `compute_peer_answer` with matching lease fields and answer SDP;
API responds with these lease fields/token/answer/device_id/timeout_ms.
Only one offer and answer per attempt; no large data is relayed by signaling.
No automatic TURN relay on this server. Default public STUN is
`stun:stun.cloudflare.com:3478`; failed NAT traversal falls back to source.

DataChannel label is `robopark-compute-v1`, reliable ordered, one per connection/lease. Each direction
sends one JSON header `{type: 'begin', bytes: N}` followed by binary chunks <=16KiB
and JSON `{type:'end'}`; sender honors bufferedAmount<=256KiB. Request transfer is
UTF-8 JSON `{token, frame:<compute_job>}` (envelope<=8MiB), response transfer is
the normal compute_result JSON. Browser validates the actual JPEG header, bounded
image decode and hash, then POSTs `/compute/direct/<job_id>/finish` with outcome
`completed`. The server rechecks current device/account authority. Only on 2xx the
browser sends `{type:'ack', job_id, attempt_id, payload_sha256, outcome:'completed'}`
and drains the channel. Native releases its work only after this matching ACK.
Failure sends finish outcome `fallback` or `cancelled` (account change), not a
success cancellation. Native Mac host verifies token AND active lease,
all IDs, operation, digest and payload length before starting its worker. Data
is confined to an ephemeral nonpersistent hidden WKWebView served from a private
loopback capability path. No camera/microphone permission is requested.

Mac sends `compute_peer_done` over WSS with lease fields, input digest, ok,
input_bytes/output_bytes (raw payload/result byte counts), duration_ms. Server
validates lease and live authority, records only bounded metrics, releases slot.
Both API caller cancellation (DELETE `/compute/direct/<job_id>`), timeout and
compute socket close cancel direct leases and child processes. Client validates
result IDs/digest/output hash and always closes peer/channel in finally. No
completed job is reused. Revocation closes active Mac channels and cancels work.

Transport sources: WebKit confirms RTCDataChannel in WKWebView
(https://webkit.org/blog/7763/a-closer-look-into-webrtc/); W3C specifies SCTP
message limits/backpressure (https://www.w3.org/TR/webrtc/); public STUN address
from Cloudflare (https://developers.cloudflare.com/realtime/sfu/get-started/connection-patterns/).

## Work and boundaries

Two application workflows can use the pool: photo preparation before upload
and inventory CSV rendering after the server has authorized and captured rows.
CSV relay is opt-in (`COMPUTE_CSV_OFFLOAD_ENABLED=true`, default false): ordinary
CSV formatting is cheap, and sending rows plus results would increase server
Wi-Fi traffic. Local/direct photo processing remains enabled with the Mac pool.
Typed operations are `image_resize_v1` and `csv_render_v1`. No remote shell,
downloaded executable, arbitrary URL/path, DB credential or corporate cookie is
part of a compute job. Macs remain trusted by the owner's policy, with approved
active service accounts, immutable device ownership and revocation enforced.
Emergency access is independent of compute eligibility. Public submissions are
authenticated and bounded; inventory authorization stays in the existing route.

The shared broker service hosts a separate compute WebSocket and scheduler so
CPU work cannot consume Emergency upstream quotas or require corporate SSO.
The existing HTTPS origin routes `/api/emergency/bridge/compute/ws` to the broker
path `/emergency/bridge/compute/ws`. There are no new public ports. TLS and the
existing pinned test assignment protect transport. P256 device authentication
uses a distinct signed string `robopark-compute-v1\n<instance>\n<environment>\n<nonce>`.
Only existing registered, non-revoked devices and approved active owners connect.

## Wire contract

All frames are JSON. Challenge/auth/ready match the existing bridge shape but
use the compute signing domain above. Ready supplies a unique `generation` and
heartbeat interval. Heartbeat fields: generation, capacity (0..2), active_jobs
(0..2), cpu_percent, memory_percent, thermal_state, power, supports (operation
names), version. A fresh authenticated compute channel is independent of
Emergency readiness. Stale heartbeat (>30sec), overload, pause, logout, sleep or
revocation prevents new work.

Job frame: `type=compute_job`, generation, job_id UUID, attempt_id UUID,
operation, payload_base64, payload_sha256, timeout_ms. The hash covers exact
decoded UTF-8 JSON bytes, so no cross-language canonicalization is needed.
Result: `type=compute_result`, generation, job_id, attempt_id,
payload_sha256 (echo input digest), ok boolean, result or bounded error code.
Result object contains data_base64, sha256 of decoded output bytes, mime_type,
and for images width/height. Cancel echoes generation/job_id/attempt_id.
Only the currently leased device/generation/attempt may finish a job; late or
duplicate results cannot commit. Disconnect/timeout retries another device at
most once within the original deadline; clients retain local fallback.

Image payload: data_base64, mime_type (`image/jpeg`, `image/png`, `image/webp`),
max_edge integer 64..4096, quality integer 40..95. Maximum input image 4MiB,
maximum decoded image 40 million pixels. Output is oriented JPEG, metadata
stripped, no upscaling, bounded dimensions and <=4MiB. The caller keeps original
when output would grow, fail validation or exceed its deadline. QR/document
flows keep original bytes. Larger photos keep the existing local worker path.

CSV payload: headers (1..20 strings), rows (<=10000, matching header width),
each cell already converted to a string and formula-escaped by the authoritative
server. UTF-8 BOM, comma delimiter, CRLF, RFC4180 quoting identical to existing
csv.writer. Result MIME `text/csv; charset=utf-8`, <=8MiB. Existing streaming
export remains fallback for oversized data and unavailable pool.

WSS job envelope <=9MiB; payload JSON <=6MiB, decoded output <=8MiB (image <=4MiB).
WSS result envelope <=12MiB includes base64/JSON overhead; the direct image-only
DataChannel envelope stays <=8MiB.
100 connected devices, 12 pending jobs globally, 2 per requester, and bounded
aggregate payload memory (48MiB). Default deadline 20sec, max30sec; no ready
device returns promptly. No persistent mutation is performed by workers: a
broker restart fails transient work safely into the existing caller fallback.

## Execution and user experience

Swift `RoboparkComputeWorker` is bundled inside the app and runs one job per
process at utility priority; stdin/stdout are bounded JSON, no shell. The parent
kills a cancelled/timed-out child. ImageIO performs actual decoding/resizing;
Foundation renders CSV. One compute slot by default, no new work above CPU70%,
memory80%, serious/critical thermal or low power; Emergency jobs take priority.
The service panel shows server, sync, Emergency and compute statuses separately,
active/completed work and clear recovery errors without exposing configuration.
Logout/sleep/quit stops both channels and child processes. Startup and wake
restore the authorized account and reconnect. Update status distinguishes an
installed update from the version currently running.

## Acceptance

Prove cross-device execution with real Swift workers through a local broker;
two workers, failover, cancellation, forged/stale result, bad hashes, resource
draining, revocation and 0/1/2/12/100 client cases. Exercise actual image pixels
and CSV fidelity, browser upload fallback and real inventory export routes.
Test packaged app lifecycle and isolated launch where possible without touching
user Keychain/LaunchAgents. Run targeted and full relevant suites, independent
review, build all final packages from clean commit, publish only prerelease
0.2.3-rc.2 with latest=false and verify uploaded hashes. Real corporate SSO and
remote physical Khadas remain field checks unless access is available. Do not
claim that all traffic bypasses Wi-Fi: direct photos bypass server processing
traffic, CSV uses bounded relay and final authoritative uploads still use it.

## Desktop distribution and notifications

Mac is the full workspace for operators, admins and royal, replacing their web/PWA
entry point while all three clients remain supported. Bundled UI, protected
offline data and queued supported actions do not depend on website static assets.
The service status panel is secondary. A single `.command` embeds the pkg for
local install/reinstall/update; automatic online updates use the signed test/rc
catalog. No global Gatekeeper changes or claims of Apple notarization. App menu
removal stops helpers after a lifecycle retirement fence; data removal is explicit.

Existing role/park-scoped task, return, comment, report and review inbox events
also appear in macOS notifications when granted. Announcements, calls to action
and maintenance messages use the same inbox; system/network incidents and recovery
are royal-only. Deduplicate events, fence account changes, suspend on sleep,
back off failures and keep the inbox usable when system notifications are denied.

## Verified native direct path, 15 October 2026

The macOS harness runs two real WKWebViews on separate loopback HTTP origins,
validates the lease at the native host, invokes the bundled Swift ImageIO worker,
and checks actual JPEG dimensions, digest and the final ACK. Two consecutive jobs
passed with production Cloudflare STUN and unmodified network ICE candidates
(`ComputePeerChecks WORKER PEER_HTML --stun`). The no-STUN mode timed out on this
host; this is not evidence of the cause or of connectivity across different NATs.

An explicit test-only `--loopback-candidates` mode also passed two jobs. That mode
rewrites candidates only in the test executable and is never used by the app. It
exposed and verified the fix for a fast-channel ACK race: the sender now enters
ACK-waiting state synchronously after queuing the final frame, before awaiting
buffer drain. Neither test replaces acceptance between physical Macs on separate
networks. Field failures retain the local/original fallback, without relaying the
original photo through the server for processing.
