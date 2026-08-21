# Robopark Platform Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a web-first platform skeleton — FastAPI + React monorepo, session auth, empty role cabinets, SQLite on host, one Docker Compose with `ROBOPARK_ROLE=host|vps` — with no Tracker/Emergency/Telegram code.

**Architecture:** Brain (API + SQLite) runs only on the Armbian host; VPS compose starts reverse proxy + tunnel templates only. SPA talks to `/api/*` via same-origin proxy in prod and Vite proxy in local dev. Auth is username/password with server-side sessions in SQLite and an httpOnly cookie.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.x, Alembic, argon2-cffi, pytest + httpx; React 19 + Vite + TypeScript + React Router; Docker Compose profiles; Caddy (or nginx) templates under `deploy/`.

**Origin:** Approved design `docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md`; handoff `docs/superpowers/handoffs/2026-08-21-platform-phase1.md`.

## Global Constraints

- Clean slate: do **not** import or vendor code from `robopark_tehblokdan`.
- No Telegram bots, Tracker, Emergency, reports, blockers, SLA, or onboarding flows in Phase 1.
- Roles only: `royal`, `admin`, `operator`, `mechanic` (string enum on `users.role`).
- Secrets only via env / `.env` (gitignored); never commit real passwords.
- `ROBOPARK_ROLE=host` runs API (+ web); `ROBOPARK_ROLE=vps` must **not** start API or mount the app DB.
- SQLite path on host volume via env; SQLAlchemy + Alembic so Postgres can swap later.
- Cookie session: httpOnly, Secure in production, `SameSite=Lax` (same-site via reverse proxy).
- API prefix for browser calls in prod: mount app routes under `/api` **or** proxy `/api` → API root; plan locks **proxy strip**: public `/api/health` → container `/health`. Local Vite proxies `/api` → `http://127.0.0.1:8000`.

## File structure (create)

```
robopark/
  apps/api/
    pyproject.toml
    alembic.ini
    alembic/env.py
    alembic/versions/0001_create_users_sessions.py
    src/robopark_api/
      __init__.py
      main.py
      config.py
      db.py
      models.py
      schemas.py
      security.py
      deps.py
      seed.py
      routers/health.py
      routers/auth.py
    tests/
      conftest.py
      test_health.py
      test_auth.py
  apps/web/
    package.json
    vite.config.ts
    tsconfig.json
    index.html
    src/main.tsx
    src/App.tsx
    src/api.ts
    src/auth.tsx
    src/pages/Login.tsx
    src/pages/Admin.tsx
    src/pages/Operator.tsx
    src/pages/Mechanic.tsx
    src/pages/Home.tsx
  deploy/
    docker-compose.yml
    Caddyfile.vps.example
    tunnel.env.example
    host.env.example
    vps.env.example
  .env.example
  README.md
```

---

### Task 1: API scaffold + `/health`

**Files:**
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/src/robopark_api/__init__.py`
- Create: `apps/api/src/robopark_api/config.py`
- Create: `apps/api/src/robopark_api/main.py`
- Create: `apps/api/src/robopark_api/routers/health.py`
- Create: `apps/api/tests/conftest.py`
- Create: `apps/api/tests/test_health.py`

**Interfaces:**
- Consumes: nothing
- Produces: FastAPI app factory `create_app() -> FastAPI`; `GET /health` → `{"status": "ok"}`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_health.py
from fastapi.testclient import TestClient

from robopark_api.main import create_app


def test_health_returns_ok():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && pytest tests/test_health.py -v`

Expected: FAIL (module or package missing) until scaffold exists; after creating empty package without route, FAIL with 404.

- [ ] **Step 3: Write minimal implementation**

`apps/api/pyproject.toml`:

```toml
[project]
name = "robopark-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.32.0",
  "sqlalchemy>=2.0.36",
  "alembic>=1.14.0",
  "argon2-cffi>=23.1.0",
  "pydantic-settings>=2.6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.3.0", "httpx>=0.28.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/robopark_api"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

`apps/api/src/robopark_api/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/robopark.db"
    session_cookie_name: str = "robopark_session"
    session_ttl_seconds: int = 60 * 60 * 24 * 14
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cors_origins: str = "http://localhost:5173"
    seed_username: str | None = None
    seed_password: str | None = None
    seed_role: str = "royal"


def get_settings() -> Settings:
    return Settings()
```

`apps/api/src/robopark_api/routers/health.py`:

```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

`apps/api/src/robopark_api/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from robopark_api.config import get_settings
from robopark_api.routers import health


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Robopark API", version="0.1.0")
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
```

`apps/api/src/robopark_api/__init__.py`: empty.

`apps/api/tests/conftest.py`: empty for now (or leave as a one-line comment placeholder file — prefer empty `pass` not needed; file may omit until Task 2).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && source .venv/bin/activate && pytest tests/test_health.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
gitarius do "commit Phase 1 API health scaffold"
# or:
git add apps/api && gitarius commit -m "feat(api): add FastAPI scaffold with /health"
```

---

### Task 2: SQLAlchemy models + Alembic migration (`users`, `sessions`)

**Files:**
- Create: `apps/api/src/robopark_api/db.py`
- Create: `apps/api/src/robopark_api/models.py`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/script.py.mako`
- Create: `apps/api/alembic/versions/0001_create_users_sessions.py`
- Create: `apps/api/tests/test_models_migration.py`
- Modify: `apps/api/tests/conftest.py`

**Interfaces:**
- Consumes: `Settings.database_url`
- Produces:
  - `Base`, `get_engine()`, `SessionLocal`, `get_db()`
  - `User(id, username, password_hash, role, is_active, created_at)`
  - `Session(id, user_id, token_hash, expires_at, created_at)`
  - Alembic revision `0001` creating both tables

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_models_migration.py
from sqlalchemy import inspect

from robopark_api.db import get_engine
from robopark_api.models import Base


def test_metadata_has_users_and_sessions():
    table_names = set(Base.metadata.tables)
    assert "users" in table_names
    assert "sessions" in table_names


def test_create_all_builds_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Re-import settings/engine pattern used in conftest — see Step 3 for shared fixture
    from robopark_api.config import Settings
    from sqlalchemy import create_engine

    engine = create_engine(Settings().database_url, future=True)
    Base.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert names >= {"users", "sessions"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && source .venv/bin/activate && pytest tests/test_models_migration.py -v`

Expected: FAIL (import errors / missing models)

- [ ] **Step 3: Write minimal implementation**

`apps/api/src/robopark_api/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(StrEnum):
    royal = "royal"
    admin = "admin"
    operator = "operator"
    mechanic = "mechanic"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sessions: Mapped[list[Session]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="sessions")
```

`apps/api/src/robopark_api/db.py`:

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.config import get_settings

_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    future=True,
    connect_args={"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Configure Alembic (`alembic.ini` `script_location = alembic`, `sqlalchemy.url` left blank). In `alembic/env.py`, import `Base` metadata and build URL from `get_settings().database_url`. Generate revision:

```bash
cd apps/api && source .venv/bin/activate
mkdir -p data
alembic revision -m "create_users_sessions"
# Edit the generated file so upgrade() creates users + sessions matching models;
# prefer naming the file 0001_create_users_sessions.py
alembic upgrade head
```

Ensure `User`/`Session` forward refs: if Python complains about `Session` name clash with SQLAlchemy `Session`, rename ORM model to `AuthSession` **in models and all later tasks** (preferred if clash appears). Plan default: keep table `sessions`, class `AuthSession` to avoid clash:

```python
class AuthSession(Base):
    __tablename__ = "sessions"
    ...
```

Update relationship types accordingly. **Use `AuthSession` from this point forward.**

- [ ] **Step 4: Run tests**

Run: `cd apps/api && source .venv/bin/activate && pytest tests/test_models_migration.py -v`

Expected: PASS. Also `alembic upgrade head` against a temp SQLite file succeeds.

- [ ] **Step 5: Commit**

```bash
gitarius commit -m "feat(api): add users/sessions models and Alembic migration"
```

---

### Task 3: Password hashing + seed bootstrap

**Files:**
- Create: `apps/api/src/robopark_api/security.py`
- Create: `apps/api/src/robopark_api/seed.py`
- Create: `apps/api/tests/test_security_seed.py`
- Modify: `apps/api/src/robopark_api/main.py` (startup seed hook)
- Modify: `.env.example`

**Interfaces:**
- Consumes: `User`, `get_settings()`
- Produces:
  - `hash_password(plain: str) -> str`
  - `verify_password(plain: str, password_hash: str) -> bool`
  - `hash_session_token(raw: str) -> str` (SHA-256 hex)
  - `ensure_seed_user(db: Session) -> None` — creates user if `SEED_USERNAME` + `SEED_PASSWORD` set and username missing

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_security_seed.py
from robopark_api.security import hash_password, verify_password, hash_session_token


def test_password_roundtrip():
    h = hash_password("secret-pass")
    assert h != "secret-pass"
    assert verify_password("secret-pass", h)
    assert not verify_password("wrong", h)


def test_session_token_hash_is_stable_sha256():
    assert hash_session_token("abc") == hash_session_token("abc")
    assert len(hash_session_token("abc")) == 64
```

Add a DB seed test using an in-memory/tmp SQLite session (fixture in `conftest.py`):

```python
def test_ensure_seed_user_creates_once(db_session, monkeypatch):
    monkeypatch.setenv("SEED_USERNAME", "royal")
    monkeypatch.setenv("SEED_PASSWORD", "change-me")
    monkeypatch.setenv("SEED_ROLE", "royal")
    from robopark_api.config import Settings
    from robopark_api.seed import ensure_seed_user
    from robopark_api.models import User
    from sqlalchemy import select

    settings = Settings()
    ensure_seed_user(db_session, settings)
    ensure_seed_user(db_session, settings)
    users = db_session.scalars(select(User)).all()
    assert len(users) == 1
    assert users[0].username == "royal"
    assert users[0].role == "royal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/api/tests/test_security_seed.py -v` (from `apps/api` with venv)

Expected: FAIL (missing modules)

- [ ] **Step 3: Write minimal implementation**

```python
# apps/api/src/robopark_api/security.py
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, plain)
    except VerifyMismatchError:
        return False


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
```

```python
# apps/api/src/robopark_api/seed.py
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.models import User
from robopark_api.security import hash_password


def ensure_seed_user(db: Session, settings: Settings) -> None:
    if not settings.seed_username or not settings.seed_password:
        return
    existing = db.scalar(select(User).where(User.username == settings.seed_username))
    if existing:
        return
    db.add(
        User(
            username=settings.seed_username,
            password_hash=hash_password(settings.seed_password),
            role=settings.seed_role,
            is_active=True,
        )
    )
    db.commit()
```

Wire lifespan in `main.py`: open DB session, `ensure_seed_user`, close. Add `conftest.py` fixture that creates engine against `sqlite:///{tmp_path}/test.db`, `Base.metadata.create_all`, yields session.

Update `.env.example`:

```bash
# API
DATABASE_URL=sqlite:///./data/robopark.db
CORS_ORIGINS=http://localhost:5173
SESSION_COOKIE_NAME=robopark_session
COOKIE_SECURE=false
COOKIE_SAMESITE=lax

# First boot only — change immediately; never commit real values
SEED_USERNAME=royal
SEED_PASSWORD=
SEED_ROLE=royal

# Docker role: host | vps
ROBOPARK_ROLE=
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_security_seed.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
gitarius commit -m "feat(api): add argon2 passwords and optional seed user"
```

---

### Task 4: Session auth routes (`/auth/login`, `/auth/logout`, `/auth/me`)

**Files:**
- Create: `apps/api/src/robopark_api/schemas.py`
- Create: `apps/api/src/robopark_api/deps.py`
- Create: `apps/api/src/robopark_api/routers/auth.py`
- Create: `apps/api/tests/test_auth.py`
- Modify: `apps/api/src/robopark_api/main.py`

**Interfaces:**
- Consumes: `User`, `AuthSession`, security helpers, `get_db`, settings
- Produces:
  - `POST /auth/login` body `{username, password}` → 204 + Set-Cookie; 401 on bad creds / inactive
  - `POST /auth/logout` → 204 + clear cookie; deletes session row if present
  - `GET /auth/me` → `{id, username, role}` or 401
  - Dependency `require_user` for later routes

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_auth.py
from fastapi.testclient import TestClient


def test_login_me_logout_flow(client: TestClient, seed_royal):
    r = client.post("/auth/login", json={"username": "royal", "password": "secret"})
    assert r.status_code == 204
    assert "robopark_session" in r.cookies

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {"id": seed_royal.id, "username": "royal", "role": "royal"}

    out = client.post("/auth/logout")
    assert out.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_login_bad_password(client: TestClient, seed_royal):
    r = client.post("/auth/login", json={"username": "royal", "password": "nope"})
    assert r.status_code == 401


def test_me_without_cookie(client: TestClient):
    assert client.get("/auth/me").status_code == 401
```

`conftest.py` provides `client` bound to tmp DB with migrations/`create_all`, and `seed_royal` user with known password `secret`.

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (404 / missing router)

- [ ] **Step 3: Write minimal implementation**

Schemas:

```python
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
```

Auth router logic (sketch — implement fully):

1. Lookup user by username; verify password; require `is_active`.
2. `raw = new_session_token()`; store `AuthSession(token_hash=hash_session_token(raw), expires_at=now+ttl)`.
3. `response.set_cookie(settings.session_cookie_name, raw, httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite, max_age=settings.session_ttl_seconds, path="/")`.
4. `require_user`: read cookie → hash → lookup non-expired session → load user → else 401.
5. Logout: delete session by token hash; `delete_cookie`.

Include `auth.router` in `create_app()`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_auth.py tests/test_health.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
gitarius commit -m "feat(api): add cookie session login/logout/me"
```

---

### Task 5: Web app scaffold (Vite + React + login)

**Files:**
- Create: entire `apps/web/` as listed in File structure (except cabinet pages can be stubs)
- Create: `apps/web/src/api.ts`, `apps/web/src/auth.tsx`, `apps/web/src/pages/Login.tsx`, `apps/web/src/App.tsx`, `apps/web/src/main.tsx`
- Modify: root `README.md` (local web/API commands) — may wait until Task 8 if preferred; include Vite proxy here

**Interfaces:**
- Consumes: `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout` (via Vite proxy)
- Produces: SPA with `/login`; authenticated bootstrap via `AuthProvider`

- [ ] **Step 1: Scaffold Vite app**

```bash
cd apps && npm create vite@latest web -- --template react-ts
cd web && npm install && npm install react-router-dom
```

`vite.config.ts` proxy:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
```

- [ ] **Step 2: Implement API client + auth context**

```ts
// apps/web/src/api.ts
export type User = { id: number; username: string; role: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    throw new Error(String(res.status));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  me: () => request<User>("/auth/me"),
  login: (username: string, password: string) =>
    request<void>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
};
```

`AuthProvider`: on mount call `api.me()`; expose `{user, loading, login, logout}`. Login page form posts credentials, then navigates by role:

| role | path |
|------|------|
| royal, admin | `/admin` |
| operator | `/operator` |
| mechanic | `/mechanic` |

- [ ] **Step 3: Manual smoke (dev)**

Terminal A: `cd apps/api && source .venv/bin/activate && mkdir -p data && DATABASE_URL=sqlite:///./data/robopark.db SEED_USERNAME=royal SEED_PASSWORD=secret SEED_ROLE=royal uvicorn robopark_api.main:app --reload --app-dir src`

Terminal B: `cd apps/web && npm run dev`

Open `http://localhost:5173/login`, log in as `royal` / `secret`, confirm redirect toward admin route (even if placeholder).

- [ ] **Step 4: Commit**

```bash
gitarius commit -m "feat(web): add Vite React app with session login"
```

---

### Task 6: Empty role cabinets + route guards

**Files:**
- Create: `apps/web/src/pages/Admin.tsx`
- Create: `apps/web/src/pages/Operator.tsx`
- Create: `apps/web/src/pages/Mechanic.tsx`
- Create: `apps/web/src/pages/Home.tsx`
- Modify: `apps/web/src/App.tsx`

**Interfaces:**
- Consumes: `user.role` from auth context
- Produces: routes `/admin`, `/operator`, `/mechanic`, `/` ; wrong role → redirect `/`; unauthenticated → `/login`

- [ ] **Step 1: Implement pages**

Each cabinet is a single heading only, e.g. `<h1>Admin</h1>` (likewise Operator / Mechanic). No cross-role menus. `Home` redirects logged-in users to their cabinet.

Route guard helper:

```tsx
function RequireRole({ roles, children }: { roles: string[]; children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (!roles.includes(user.role)) return <Navigate to="/" replace />;
  return children;
}
```

- `royal` and `admin` both allowed on `/admin`.
- `/operator` → `operator` only.
- `/mechanic` → `mechanic` only.

- [ ] **Step 2: Manual verify**

Log in as seed royal → `/admin` shows “Admin”. Hitting `/operator` redirects home.

- [ ] **Step 3: Commit**

```bash
gitarius commit -m "feat(web): add empty role cabinets with route guards"
```

---

### Task 7: Docker Compose `host` / `vps` roles

**Files:**
- Create: `apps/api/Dockerfile`
- Create: `apps/web/Dockerfile` (multi-stage build → static files; nginx or serve via Caddy on host profile)
- Create: `deploy/docker-compose.yml`
- Create: `deploy/host.env.example`
- Create: `deploy/vps.env.example`
- Create: `deploy/Caddyfile.vps.example`
- Create: `deploy/tunnel.env.example`
- Create: `deploy/README.md` (short role matrix)

**Interfaces:**
- Consumes: `ROBOPARK_ROLE` or Compose `--profile`
- Produces: one compose file; host profile starts `api` + `web`; vps profile starts `caddy` (+ documented tunnel sidecar placeholder) and **does not** define running `api` service under that profile

- [ ] **Step 1: API Dockerfile**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .
ENV PYTHONPATH=/app/src
CMD ["sh", "-c", "alembic upgrade head && uvicorn robopark_api.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 2: Compose with profiles**

```yaml
# deploy/docker-compose.yml
services:
  api:
    profiles: ["host"]
    build: ../apps/api
    env_file: [.env]
    environment:
      DATABASE_URL: sqlite:////data/robopark.db
    volumes:
      - robopark_data:/data
    ports:
      - "8000:8000"

  web:
    profiles: ["host"]
    build: ../apps/web
    ports:
      - "8080:80"
    depends_on: [api]

  caddy:
    profiles: ["vps"]
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile.vps.example:/etc/caddy/Caddyfile:ro
      # TLS data volume later

volumes:
  robopark_data:
```

Document required usage:

```bash
export ROBOPARK_ROLE=host   # human-facing; compose uses profiles
docker compose --profile host up -d --build

export ROBOPARK_ROLE=vps
docker compose --profile vps up -d
```

Add a tiny `deploy/check-role.sh` (optional) that exits non-zero if `ROBOPARK_ROLE=host` but user passes `--profile vps` or vice versa — only if cheap; otherwise document clearly in README.

`Caddyfile.vps.example`: reverse_proxy to tunnel upstream placeholder `host.tunnel:8000` (comment that real tunnel DNS/name is operator-filled). `tunnel.env.example`: comments for Cloudflare Tunnel / frp / wireguard — **templates only**, no live secrets.

Web Dockerfile: build SPA, nginx.conf proxies `/api/` → `http://api:8000/`.

- [ ] **Step 3: Validate compose config**

```bash
cd deploy
docker compose --profile host config >/dev/null
docker compose --profile vps config >/dev/null
# Confirm: vps config output must not include building/running api with DB volume as a vps service
```

- [ ] **Step 4: Commit**

```bash
gitarius commit -m "feat(deploy): add host/vps Docker Compose profiles"
```

---

### Task 8: README + env docs + success criteria check

**Files:**
- Modify: `README.md`
- Modify: `.env.example` (final sync with all keys)
- Modify: `apps/api` / `apps/web` package docs only if needed

**Interfaces:** none (docs)

- [ ] **Step 1: Expand README**

Must document:

1. Local API: venv, `alembic upgrade head`, uvicorn, seed env vars.
2. Local web: `npm install && npm run dev`, proxy to API.
3. Host install: clone, copy `deploy/host.env.example` → `.env`, set seed + `ROBOPARK_ROLE=host`, `docker compose --profile host up`.
4. VPS install: `ROBOPARK_ROLE=vps`, `--profile vps`, fill Caddy + tunnel templates; **API does not run on VPS**.
5. Explicit: no dependency on old bot repo; Phase 1 non-goals list (one short bullet list).

- [ ] **Step 2: End-to-end checklist (implementer runs)**

1. Fresh API+web local: seed login → correct cabinet.
2. `docker compose --profile vps config` has no `api` service enabled.
3. `GET /health`, auth routes work.
4. Delete SQLite volume/file, restart host stack → Alembic recreates schema; with seed env, user returns.
5. `gitarius scan` clean before any push.

- [ ] **Step 3: Commit**

```bash
gitarius commit -m "docs: document Phase 1 local and host/vps install"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| Monorepo `apps/api` + `apps/web` + `deploy/` | 1, 5, 7 |
| FastAPI + React/Vite | 1, 5 |
| `/health`, `/auth/login`, `/auth/logout`, `/auth/me` | 1, 4 |
| httpOnly session cookie + hashed passwords + seed users | 3, 4 |
| Roles royal/admin/operator/mechanic + empty cabinets | 2, 6 |
| SQLite + SQLAlchemy + Alembic | 2 |
| Compose host runs API+web; vps does not run API/DB | 7 |
| README + env examples; no old bot dependency | 8 |
| Non-goals (Tracker, Telegram, onboarding, …) | Global Constraints + Task 8 docs |

## Deferred (explicit)

- Live SSH deploy to Armbian/VPS (wait for user-granted access; never ask for passwords in chat).
- `GET /ready` DB ping (optional; add in Task 4 only if <15 minutes).
- Postgres cutover, Telegram clients, onboarding flows — later phases.

## Execution note

Prefer **test-first** on API tasks (1–4). Web/Docker tasks use smoke checks where unit tests would be ceremony. Do not push to origin unless the user asks; use `gitarius` for scan/commit/push.
