import base64
import hashlib
import hmac
import struct

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from robopark_api import main
from robopark_api.config import Settings, get_settings, reset_settings_cache
from robopark_api.db import get_db
from robopark_api.models import AccessStatus, Base, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.rbac_seed import ensure_rbac_catalog

#: Password satisfying the default policy (length + character classes).
#: Fixtures hash passwords directly, so only tests going through the API need it.
VALID_PASSWORD = "Str0ng-Pass!2026"


def login_as(client: TestClient, username: str, password: str):
    return client.post("/auth/login", json={"username": username, "password": password})


def _test_totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


@pytest.fixture
def authorize_privileged_ops(client, monkeypatch):
    """Exercise the real enrollment/reauth API for legacy ops route tests."""
    original_post = client.post
    clock = {"counter": 100_000, "secret": None, "current_user": None, "secrets": {}}
    monkeypatch.setattr(
        "robopark_api.services.privileged_auth._unix_time",
        lambda: float(clock["counter"] * 30),
    )

    def operation(path: str, kwargs: dict) -> tuple[str, str] | None:
        payload = kwargs.get("json") or {}
        mapping = {
            "/admin/ops/snapshot": ("snapshot", "snapshot"),
            "/admin/ops/restore": ("restore", "restore"),
            "/admin/ops/update": ("update", "update"),
            "/admin/ops/abort": ("abort", "abort"),
            "/admin/ops/repair": ("repair", "repair"),
            "/admin/ops/update/inspect": ("update.inspect", "inspect"),
            "/admin/ops/update/approve": (
                "update.approve",
                str(payload.get("inspection_id", "")),
            ),
            "/admin/ops/github-update/approve": (
                "github-update.approve",
                str(payload.get("release_id", "")),
            ),
        }
        return mapping.get(path)

    def ensure_enrolled() -> bool:
        if clock["secret"] is not None:
            return True
        started = original_post("/admin/privileged-auth/enrollment")
        if started.status_code != 200:
            return False
        clock["secret"] = started.json()["secret"]
        clock["secrets"][clock["current_user"]] = clock["secret"]
        confirmed = original_post(
            "/admin/privileged-auth/enrollment/confirm",
            json={
                "password": "secret",
                "code": _test_totp(clock["secret"], clock["counter"]),
            },
        )
        if confirmed.status_code != 200:
            return False
        clock["counter"] += 1
        return True

    def privileged_headers(kind: str, operation_id: str) -> dict[str, str]:
        if not ensure_enrolled():
            return {}
        issued = original_post(
            "/admin/privileged-auth/reauthorize",
            json={
                "password": "secret",
                "code": _test_totp(clock["secret"], clock["counter"]),
                "operation_kind": kind,
                "operation_id": operation_id,
            },
        )
        if issued.status_code != 200:
            return {}
        clock["counter"] += 1
        return {"X-Privileged-Authorization": issued.json()["token"]}

    def privileged_post(path, *args, **kwargs):
        if path == "/auth/login":
            response = original_post(path, *args, **kwargs)
            if response.status_code == 204:
                clock["current_user"] = (kwargs.get("json") or {}).get("username")
                clock["secret"] = clock["secrets"].get(clock["current_user"])
            return response
        binding = operation(path, kwargs)
        if binding is None or "X-Privileged-Authorization" in kwargs.get("headers", {}):
            return original_post(path, *args, **kwargs)
        response = original_post(path, *args, **kwargs)
        detail = (
            response.json().get("detail")
            if response.headers.get("content-type", "").startswith("application/json")
            else None
        )
        if not isinstance(detail, str) or detail not in {
            "privileged_enrollment_required",
            "privileged_authorization_required",
        }:
            return response
        if detail == "privileged_enrollment_required" and not ensure_enrolled():
            return response
        authorization = privileged_headers(*binding)
        if not authorization:
            return response
        headers = dict(kwargs.get("headers", {}))
        headers.update(authorization)
        return original_post(path, *args, **{**kwargs, "headers": headers})

    monkeypatch.setattr(client, "post", privileged_post)
    client.privileged_headers = privileged_headers
    return privileged_post


def role_id_for(db_session: Session, slug: str) -> int:
    role = rbac.get_role_by_slug(db_session, slug)
    assert role is not None, f"role {slug!r} missing from RBAC catalog"
    return role.id


@pytest.fixture(autouse=True)
def ignore_local_env_file(monkeypatch):
    """A developer's apps/api/.env must never change a test outcome.

    Production `Settings` still reads it; only the suite opts out, so that
    `monkeypatch.delenv` and explicit keyword arguments stay authoritative.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def default_test_secret_key(monkeypatch):
    """Fail-closed crypto needs a key; suite code calls get_settings() directly."""
    monkeypatch.setenv("SECRET_KEY", "test-suite-secret-key")


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """`get_settings()` is process-wide cached; isolate it between tests."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def clear_response_caches(disable_live_merge_by_default):
    """Every test starts with cold Tracker/Emergency caches — otherwise the
    monkeypatched upstream calls in the previous test would leak through the
    module-level TTL cache and mask real behaviour."""
    from robopark_api.services import emergency_cache, tracker_cache

    tracker_cache.clear_all()
    emergency_cache.clear_cache_for_tests()
    yield
    tracker_cache.clear_all()
    emergency_cache.clear_cache_for_tests()


@pytest.fixture(autouse=True)
def disable_live_merge_by_default(monkeypatch):
    """Unit tests stay in-process; multiprocess modules opt into a tmp store."""
    monkeypatch.setenv("ROBOPARK_LIVE_MERGE", "0")
    from robopark_api.services.live_merge import reset_live_merge_store

    reset_live_merge_store()
    yield
    reset_live_merge_store()


@pytest.fixture
def sqlite_database_url(tmp_path):
    return f"sqlite:///{tmp_path / 'alembic.db'}"


@pytest.fixture
def db_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    with Session(db_engine) as session:
        ensure_rbac_catalog(session)
        yield session


@pytest.fixture
def release_key_pair(tmp_path):
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_path = tmp_path / "release-public-key.pem"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_bytes, public_path


@pytest.fixture
def test_settings(db_engine, tmp_path, release_key_pair):
    (tmp_path / "host.env").write_text("SECRET_KEY=test-ops-key\n", encoding="utf-8")
    return Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        seed_username=None,
        seed_password=None,
        secret_key="test-suite-secret-key",
        report_attachments_dir=str(tmp_path / "report-attachments"),
        ops_dir=str(tmp_path / "ops"),
        ops_apply_root=str(tmp_path / "apply"),
        ops_host_env_path=str(tmp_path / "host.env"),
        ops_release_public_key_path=str(release_key_pair[1]),
        ops_sync=True,
    )


@pytest.fixture
def client(db_engine, db_session, test_settings, monkeypatch):
    # create_app's lifespan seeds through main.SessionLocal, which is bound to
    # the real DATABASE_URL. Redirect it at the tmp database before building
    # the app so entering TestClient cannot touch a developer's database.
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)

    # Route tests start only the API. Background jobs now belong to the
    # separate WorkerRuntime process and never touch this fixture's database.
    app = main.create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seed_admin(db_session):
    user = User(
        username="admin",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ADMIN),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def seed_royal(db_session):
    user = User(
        username="royal",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ROYAL),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def seed_pending_operator(db_session):
    user = User(
        username="operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.OPERATOR),
        access_status="pending",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def seed_park_with_tracker(db_session):
    park = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_blockers=True,
    )
    db_session.add(park)
    db_session.commit()
    db_session.refresh(park)
    return park


@pytest.fixture
def seed_mechanic(db_session, seed_park_with_tracker):
    user = User(
        username="mech1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.MECHANIC),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    db_session.refresh(user)
    return user
