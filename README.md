# Robopark

Web-first fleet operations system (admin / operator / mechanic).

- **Primary:** website on local host (API + app), public HTTPS via VPS tunnel (no white IP on the host).
- **Reserve:** Telegram bots with feature parity, as thin clients to the same API (not in Phase 1).
- **Clean slate:** new codebase; prior bot repo is reference only, not a dependency.

## Phase 1

Platform skeleton: FastAPI + React monorepo, session auth, empty role cabinets, SQLite on host, one Docker Compose with `ROBOPARK_ROLE=host|vps`.

Design: [`docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md`](docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md)
