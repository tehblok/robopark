# AGX performance implementation plan

**Goal:** prepare Robopark for measured operation on GEACX1 / AGX Orin 32 GB while improving bounded caching and preserving existing Khadas installations and OTA v1.

**Architecture:** extend the existing host-health projection with bounded, read-only Linux performance observations. Keep production tuning conservative; extend the existing isolated capacity harness to compare the actual 2/4-process profiles instead of introducing another load generator. Fix the browser validator cache independently.

**Tech stack:** Python stdlib host tools, FastAPI, PostgreSQL, TypeScript/React, pytest and Vitest.

**Spec:** user-approved directions in this conversation and `2026-10-03-geacx1-storage-design.md`, performance section.

## Constraints

- No eight-hour soak, live production load, disk formatting, power-mode changes, or GPU dependencies.
- Hardware readings unavailable on a board are unknown, never fabricated zero/healthy values.
- Preserve unsigned/hash-verified OTA v1, migration head and unmanaged Khadas behavior.
- Never publish SSIDs, MAC/IP addresses, device serials, raw command output or credentials.
- No speculative PostgreSQL memory/index changes without query/load evidence.
- Existing authorization, storage guards and cache invalidation remain authoritative.

## Review focus

1. Missing/invalid/stale sysfs/proc readings must not break watchdog or look healthy.
2. Multi-interface Wi-Fi and incomplete GPU sensors must not publish misleading health or identifiers.
3. An expired/evicted ETag body must never be reused after an unrelated response or logout.
4. Four-worker validation must actually observe four workers and remain isolated from production.
5. CPU power modes must not silently misrepresent the measured hardware or cause aggressive automatic tuning.

## Tasks

- [x] Add bounded performance collector, watchdog publication, validated API projection and system-page rendering. Test malformed/absent readings, privacy, stale snapshots and integration failures before implementation.
- [x] Bound revision validator cache by TTL/count/bytes, preserve 304 semantics and auth cleanup; reproduce failure through real request behavior and run browser regressions.
- [x] Precompress immutable JS/CSS/SVG at build time and serve negotiated gzip through nginx, retaining uncompressed fallback and existing PWA/HTML cache rules. Verify actual HTTP headers and decompressed bytes.
- [x] Extend `scripts/capacity_benchmark.py` with explicit 2/4-worker selection and matching PostgreSQL profile settings, recording them in reports. Add real acceptance helper tests before implementation. Reuse existing isolated PostgreSQL harness and keep production URLs unsupported.
- [x] Document short measurements and hardware-dependent tuning, review independent changes, run affected API/web checks and full host gate on frozen sources. Hardware acceptance stays explicit. Evidence: `../reviews/2026-10-03-agx-performance.md`; publication uses the verified commit on main and a separately verified local OTA candidate.

## Acceptance

The system page shows timestamped optional measurements without privileged commands from the browser. Cache retention remains bounded without compromising authorization or validator correctness. Capacity reports identify the selected profile and reject incomplete worker coverage. No claim of AGX speedup is made until the same workload is run on physical hardware.
