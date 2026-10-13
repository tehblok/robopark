# Mac Offline and Shared Pool Implementation Plan

> **For agentic workers:** use superpowers:executing-plans for each owned area.

**Goal:** Ship a testable offline Mac workspace and fair, bounded shared Emergency pool.
**Architecture:** Bundled existing React workspace, native loopback service and
Keychain session, existing scoped IndexedDB/idempotent sync, enhanced WSS broker.
**Tech Stack:** Swift/Foundation/Network/WebKit/CryptoKit/Compression; React/TS;
Python/FastAPI/PostgreSQL.
**Spec:** docs/architecture/2026-10-13-macos-offline-pool.md

## Constraints

- macOS 13+, ARM64, one pkg, no runtime dependency installation.
- 0–100 connected devices, at most two Emergency attempts per request.
- Default trust only for authenticated, active approved account enrollment.
- Existing account/park/VIN access and offline identity window retained.
- No inbound Mac network ports; HTTP listens only on 127.0.0.1.
- No physical deployment, no secrets in logs/artifacts, no arbitrary job execution.

## Review focus

- Cross-account/server cached data or sessions surviving identity changes.
- Cold offline startup (assets, encryption key and IndexedDB opening order).
- Simultaneous 100-agent probes and rapid reconnect/late replies.
- Saturated/slow pool causing duplicate upstream traffic or queue growth.
- HTTP parser/session bypass and compressed data exceeding decoded bounds.

## 1. Web offline workspace (worker ownership: apps/web)

- [x] Add failing tests for durable per-install identity, preparation/freshness,
      scope cleanup, offline cold start and native encrypted storage adapter.
- [x] Implement an explicit bounded preparation flow using authorized existing
      read models and resource cache; preserve existing queue/revision semantics.
- [x] Integrate desktop bootstrap (`window.__ROBOPARK_DESKTOP__`) and
      `/__robopark/storage-key` (base64 AES-256 key, local authenticated request).
- [x] Test supported queue actions/reconnect and preparation interruption;
      run scoped unit tests, lint, production build and offline browser E2E.

## 2. Broker and enrollment (worker ownership: apps/api)

- [x] Add failing default trust, fairness/capacity, bounded queue, 100-node probe
      rate, compressed response validation and metrics tests.
- [x] Implement auto-approved claim, scheduler with aging and observed latency,
      global rate budget, bounded wait/backpressure and device telemetry.
- [x] Agree heartbeat/probe/compressed-result wire fields with native owner;
      maintain older pilot clients where safe.
- [x] Run targeted backend tests from apps/api; report evidence and boundaries.

## 3. Native workspace and agent (parent ownership: apps/macos, build scripts)

- [x] Add Foundation-level protocol, local HTTP and encrypted-session tests.
- [x] Implement protected local HTTP assets/proxy, Keychain sessions, bundled
      WKWebView workspace and automatic enrollment after approved account login.
- [x] Implement capacity/probe negotiation, power limits, raw DEFLATE results,
      pause/reconnect and local diagnostics with no corporate payload logs.
- [x] Bundle a freshly built workspace, offline assets, and source provenance.
- [x] Run Swift checks and app smoke tests without installation.

## 4. Integration, independent review and delivery

- [x] Run real 100-agent broker simulation, verify deduplication and rate limits,
      capture load/bytes results with synthetic payloads.
- [x] Run appropriate host/API/PG/web checks and independent code review;
      fix regressions and add reproducing tests.
- [x] Update version/release notes/runbook and recorded limitations.
- [ ] Commit reviewed source; build pkg and OTA from same clean commit (final artifact report records completion).
- [ ] Inspect actual artifacts, checksums/signatures and bundled web resources;
      save release and verification report in ignored artifacts directory.

## Source freeze evidence

API 3,104 passed, PostgreSQL 33 passed, web 2,976 passed; desktop browser
24 passed across Chromium and WebKit. Native AgentCore 43, Desktop 36,
WorkspaceSession 16 and Bridge lifecycle 8 checks passed. Independent review
findings were fixed. See docs/verification/2026-10-13-macos-offline-pool.md
for scope and the generated artifacts/rc44-offline-pool/verification report
for post-commit package and OTA evidence. No physical deployment performed.
