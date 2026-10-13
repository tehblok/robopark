from fastapi import FastAPI
from fastapi.testclient import TestClient

from robopark_api.routers import agent_distribution


def test_distribution_is_independent_of_frontend_and_has_bounded_file_allowlist(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_distribution, "distribution_root", lambda: tmp_path)
    (tmp_path / "catalog.json").write_text('{"payload":"test","signature":"test"}')
    (tmp_path / "Robopark-0.2.0-rc.44-arm64.pkg").write_bytes(b"package")
    (tmp_path / "secret.key").write_bytes(b"never serve")
    app = FastAPI()
    app.include_router(agent_distribution.router)
    with TestClient(app) as client:
        catalog = client.get("/distribution/v1/macos/catalog.json")
        assert catalog.status_code == 200
        assert catalog.headers["cache-control"] == "no-store"
        package = client.get("/distribution/v1/macos/Robopark-0.2.0-rc.44-arm64.pkg")
        assert package.status_code == 200
        assert package.content == b"package"
        for path in ("secret.key", "../secret.key", "%2e%2e%2fsecret.key", "update-root.json"):
            assert client.get("/distribution/v1/macos/" + path).status_code == 404


def test_distribution_rejects_symlinks_and_oversized_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_distribution, "distribution_root", lambda: tmp_path)
    outside = tmp_path.parent / "private-test-data"
    outside.write_bytes(b"not a package")
    (tmp_path / "Robopark-1.0.0-arm64.pkg").symlink_to(outside)
    (tmp_path / "catalog.json").write_bytes(b"x" * (256 * 1024 + 1))
    app = FastAPI()
    app.include_router(agent_distribution.router)
    with TestClient(app) as client:
        assert client.get("/distribution/v1/macos/Robopark-1.0.0-arm64.pkg").status_code == 404
        assert client.get("/distribution/v1/macos/catalog.json").status_code == 404
