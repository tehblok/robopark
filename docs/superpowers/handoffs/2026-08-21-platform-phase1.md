---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-21T19:46:00Z"
title: "Robopark Phase 1 platform skeleton"
summary: "Clean-slate web platform: FastAPI+React monorepo, host/vps Docker roles; spec approved; next is implementation plan then code."
keywords: ["robopark", "phase1", "fastapi", "react", "docker", "handoff"]
cwd: "/Users/tehblokdan/Desktop/Проекты/robopark"
resume_focus: "Write Phase 1 implementation plan from the approved design spec, then implement the skeleton (do not use the old bot repo as a dependency)."
repository: "daniil-kolesnik/robopark"
repo_root_sha: "14bf687"
branch: "main"
head: "834125b"
---

# Handoff: Robopark Phase 1

## Objective

Build **Robopark** as a **web-first** fleet system (admin / operator / mechanic). Telegram bots are a **later reserve client** with full API parity — **no bot code in Phase 1**.

## Where you are

- Workspace must be: `/Users/tehblokdan/Desktop/Проекты/robopark`
- Remote: `https://origin.cursor.com/daniil-kolesnik/robopark.git`
- Page: https://cursor.com/codebase/daniil-kolesnik/robopark
- Local `main` may be **1 commit ahead** of origin (design commit not pushed unless user asks)
- **Do not** continue work inside `robopark_tehblokdan` except as read-only domain reference

## Authoritative spec

`docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md` (approved)

Locked choices:

- Brain on **Armbian host** (no white IP); **VPS** = TLS + reverse proxy + tunnel
- Stack: **FastAPI + React/Vite**, monorepo `apps/api` + `apps/web` + `deploy/`
- Auth Phase 1: username/password + httpOnly session cookie; seed users
- DB: **SQLite now**, SQLAlchemy/Alembic path to Postgres later
- Install: **one Docker Compose**, required `ROBOPARK_ROLE=host|vps` (host runs API+DB; vps must not)
- Old bot onboarding (operator password→admin approve, mechanic park password, Requests inbox) = **later phases**

## Explicitly out of Phase 1

Tracker, Emergency, reports, blockers, SLA, Telegram, live deploy until user grants SSH access.

## Access

User offered Armbian + Ubuntu VPS later; **do not ask for passwords in chat**. When deploy is needed, user will grant SSH.

## Next steps (in order)

1. Invoke **writing-plans** (or equivalent) → `docs/superpowers/plans/2026-08-21-robopark-platform-phase1.md`
2. Implement skeleton per plan (TDD where sensible)
3. Push to origin only if user asks
4. Deploy when user provides host/VPS access

## Reference only (old repo)

`/Users/tehblokdan/Desktop/Проекты/robopark_tehblokdan` — roles/mechanic/Emergency designs and working Tracker knowledge. **Do not copy remnant bot wiring into the new tree.**
