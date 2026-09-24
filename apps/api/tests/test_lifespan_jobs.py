"""The HTTP application stays available without a background worker."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from robopark_api import main
from robopark_api.db import get_db
from robopark_api.services import live_merge


def test_api_lifespan_never_acquires_job_lease_or_starts_jobs(
    db_engine, test_settings, monkeypatch
):
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)

    def unexpected_lease(_self):
        raise AssertionError("API attempted to own background jobs")

    monkeypatch.setattr(live_merge.JobLease, "try_acquire", unexpected_lease)

    app = main.create_app()

    def database():
        with sessionmaker(bind=db_engine, future=True)() as session:
            yield session

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 200
