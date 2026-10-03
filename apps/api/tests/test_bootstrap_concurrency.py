"""Startup must not seed the same database from two API workers at once."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock
from time import sleep
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from robopark_api.config import Settings
from robopark_api.services import bootstrap


def test_concurrent_worker_startup_serializes_database_seed(db_engine, monkeypatch):
    entered = Event()
    release_first = Event()
    counter = Lock()
    active = 0
    maximum = 0

    def guarded_seed(_db):
        nonlocal active, maximum
        with counter:
            active += 1
            maximum = max(maximum, active)
        entered.set()
        release_first.wait(timeout=1)
        with counter:
            active -= 1

    monkeypatch.setattr(bootstrap, "ensure_rbac_catalog", lambda _db: None)
    monkeypatch.setattr(bootstrap, "ensure_default_section_roles", guarded_seed)
    monkeypatch.setattr(bootstrap.settings_svc, "migrate_plaintext_secrets", lambda _db: 0)
    monkeypatch.setattr(
        bootstrap.settings_svc, "migrate_registration_password_from_env", lambda _db: False
    )
    factory = sessionmaker(bind=db_engine, future=True)
    settings = Settings(_env_file=None, seed_username=None, seed_password=None)

    def start_worker():
        bootstrap.initialize_data(
            factory, settings, seed_user=lambda *_: None, dev_seed=lambda *_: None
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(start_worker)
        assert entered.wait(timeout=1)
        second = executor.submit(start_worker)
        sleep(0.05)
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert maximum == 1


def test_postgres_startup_lock_survives_seed_commits(monkeypatch):
    events = []

    class LockConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            events.append("connection_closed")

        @contextmanager
        def begin(self):
            events.append("transaction_begin")
            try:
                yield
            finally:
                events.append("transaction_end")

        def execute(self, statement):
            events.append(str(statement))

    class Bind:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return LockConnection()

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_bind(self):
            return Bind()

    monkeypatch.setattr(bootstrap, "ensure_rbac_catalog", lambda _db: events.append("seed"))
    monkeypatch.setattr(bootstrap, "ensure_default_section_roles", lambda _db: None)
    monkeypatch.setattr(bootstrap.settings_svc, "migrate_plaintext_secrets", lambda _db: 0)
    monkeypatch.setattr(
        bootstrap.settings_svc, "migrate_registration_password_from_env", lambda _db: False
    )

    bootstrap.initialize_data(
        Session,
        Settings(_env_file=None, seed_username=None, seed_password=None),
        seed_user=lambda *_: None,
        dev_seed=lambda *_: None,
    )

    lock_index = next(i for i, event in enumerate(events) if "pg_advisory_xact_lock" in event)
    assert events.index("transaction_begin") < lock_index < events.index("seed")
    assert events.index("seed") < events.index("transaction_end")
    assert "lock_timeout" in events[1]
